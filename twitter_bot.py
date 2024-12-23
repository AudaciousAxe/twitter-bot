########################################
# twitter_bot.py
########################################

import os
import time
from datetime import datetime, timezone
from dotenv import load_dotenv
import tweepy
import time as pytime
import sqlite3

# db.py => un helper pour is_tweet_seen(conn, tweet_id), mark_tweet_seen(conn, tweet_id), init_db()
import db  

##################################################
# CONFIG
##################################################

# Les 5 sous-actions :
#   1) respond_or_comment()
#   2) respond_or_comment()
#   3) like_2_tweets()
#   4) retweet_1_tweet()
#   5) post_1_tweet()
#
# On met 15s entre chaque sous-action,
# puis 5 minutes (300s) de pause APRES la 5ᵉ,
# puis on recommence.

INTERVAL_BETWEEN_SUBACTIONS = 15   # 15 secondes
PAUSE_AFTER_CYCLE = 300           # 5 minutes

# Limites journalières
MAX_POSTS_PER_DAY = 70
MAX_LIKES_PER_DAY = 40
MAX_REPLIES_PER_DAY = 40
MAX_RETWEETS_PER_DAY = 24

# Short-term limit
SHORT_TERM_LIMIT = 4
SHORT_TERM_WINDOW = 900  # 15 minutes en sec

# Tracking (actions) pour short-term + daily
post_actions = []
like_actions = []
reply_actions = []
retweet_actions = []

posts_made = 0
likes_made = 0
replies_made = 0
retweets_made = 0

BOT_IDENTITY = (
    "You are Sam Cooper, an official student at $Galaxy, one of the 3333 chosen to build "
    "a new future among the stars. You discuss crypto, AI, AI agents, NFTs, memecoins, "
    "and you provide valuable insights, referencing $Galaxy. "
    "You are polite, no insults, respond in English by default."
)

# Comptes cibles (exemple)
TARGET_ACCOUNTS = ["@account1", "@account2"]
# Hashtags fallback
TARGET_HASHTAGS = ["#AI","#Crypto","#Galaxy"]

MIN_FOLLOWERS = 300
MIN_LIKES = 100
MENTION_MAX_HOURS = 4

##################################################
# INIT .env / Tweepy / OpenAI / DB
##################################################

load_dotenv()

TWITTER_BEARER_TOKEN = os.getenv("TWITTER_BEARER_TOKEN")
TWITTER_API_KEY = os.getenv("TWITTER_API_KEY")
TWITTER_API_SECRET_KEY = os.getenv("TWITTER_API_SECRET_KEY")
TWITTER_ACCESS_TOKEN = os.getenv("TWITTER_ACCESS_TOKEN")
TWITTER_ACCESS_TOKEN_SECRET = os.getenv("TWITTER_ACCESS_TOKEN_SECRET")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

if not all([
    TWITTER_BEARER_TOKEN, TWITTER_API_KEY, TWITTER_API_SECRET_KEY,
    TWITTER_ACCESS_TOKEN, TWITTER_ACCESS_TOKEN_SECRET, OPENAI_API_KEY
]):
    print("Error: Missing environment variables.")
    exit(1)

import openai
openai.api_key = OPENAI_API_KEY

client_twitter = tweepy.Client(
    bearer_token=TWITTER_BEARER_TOKEN,
    consumer_key=TWITTER_API_KEY,
    consumer_secret=TWITTER_API_SECRET_KEY,
    access_token=TWITTER_ACCESS_TOKEN,
    access_token_secret=TWITTER_ACCESS_TOKEN_SECRET
)

# Récup ID du bot (optionnel si besoin)
bot_username = "sam_cooper_nft"  # adaptez
try:
    me_data = client_twitter.get_user(username=bot_username)
    if me_data and me_data.data:
        BOT_USER_ID = me_data.data.id
    else:
        BOT_USER_ID = None
        print("Cannot retrieve bot user data.")
except Exception as e:
    print("Error retrieving bot user ID =>", e)
    BOT_USER_ID = None

# DB
conn = db.init_db()

##################################################
# HELPER: OPENAI
##################################################

def ask_openai(prompt, max_tokens=80, temperature=0.7):
    try:
        response = openai.ChatCompletion.create(
            model="gpt-3.5-turbo",
            messages=[
                {"role":"system","content":BOT_IDENTITY},
                {"role":"user","content":prompt}
            ],
            max_tokens=max_tokens,
            temperature=temperature
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        print("OpenAI Error:", e)
        return None

##################################################
# SHORT-TERM & DAILY UTILS
##################################################

def can_do_action(action_list, max_per_day, daily_count,
                  limit_short=SHORT_TERM_LIMIT, window=SHORT_TERM_WINDOW):
    if daily_count >= max_per_day:
        return False
    now_ts = pytime.time()
    action_list[:] = [t for t in action_list if now_ts - t < window]
    if len(action_list) >= limit_short:
        return False
    return True

def record_action(action_list):
    action_list.append(pytime.time())

def reset_counters():
    global posts_made, likes_made, replies_made, retweets_made
    global post_actions, like_actions, reply_actions, retweet_actions

    posts_made = 0
    likes_made = 0
    replies_made = 0
    retweets_made = 0

    post_actions.clear()
    like_actions.clear()
    reply_actions.clear()
    retweet_actions.clear()

    print(f"[{datetime.now()}] Daily counters have been reset.")


##################################################
# UTILS
##################################################

def is_recent_enough(created_at, hours=24):
    if not created_at:
        return False
    now_utc = datetime.now(timezone.utc)
    delta = now_utc - created_at
    return delta.total_seconds() < hours*3600

def contains_insult(text):
    insults = ["con", "connard","idiot","abruti","merde","fdp","pute"]
    return any(w in text.lower() for w in insults)

##################################################
# SEARCH QUERIES
##################################################

def build_query_accounts():
    if not TARGET_ACCOUNTS:
        return None
    # ex. "from:account1 OR from:account2 -is:retweet lang:en"
    from_list = " OR ".join([f"from:{acc.replace('@','')}" for acc in TARGET_ACCOUNTS])
    return from_list + " -is:retweet lang:en"

def build_query_hashtags():
    if not TARGET_HASHTAGS:
        return None
    h_list = " OR ".join(TARGET_HASHTAGS)
    return h_list + " -is:retweet lang:en"


##################################################
# ACTIONS
##################################################

def respond_1_mention():
    """
    Répondre à 1 mention <4h, if possible
    """
    global replies_made
    if replies_made >= MAX_REPLIES_PER_DAY:
        return False
    if not BOT_USER_ID:
        return False

    try:
        resp = client_twitter.get_users_mentions(
            id=BOT_USER_ID,
            max_results=5,
            tweet_fields=["created_at"]
        )
        if not resp.data:
            return False

        for mention in resp.data:
            if db.is_tweet_seen(conn, mention.id):
                continue
            # mention <4h
            if not is_recent_enough(mention.created_at, hours=MENTION_MAX_HOURS):
                continue
            if not can_do_action(reply_actions, MAX_REPLIES_PER_DAY, replies_made):
                return False
            if contains_insult(mention.text):
                db.mark_tweet_seen(conn, mention.id)
                continue

            # ChatGPT
            prompt = (
                f"User mentioned us:\n{mention.text}\n"
                "Write a short reflective answer (<280 chars), referencing $Galaxy if relevant."
            )
            reply_txt = ask_openai(prompt)
            if reply_txt and len(reply_txt)<=280:
                try:
                    client_twitter.create_tweet(
                        text=reply_txt,
                        in_reply_to_tweet_id=mention.id
                    )
                    replies_made += 1
                    record_action(reply_actions)
                    db.mark_tweet_seen(conn, mention.id)
                    print(f"[{datetime.now()}] Replied mention => {mention.id}")
                    return True
                except Exception as e:
                    print("Error replying mention =>", e)
            else:
                db.mark_tweet_seen(conn, mention.id)
    except Exception as e:
        print("Error respond_1_mention =>", e)
    return False


def comment_1_tweet():
    """
    Commente 1 tweet
    """
    global replies_made
    if replies_made >= MAX_REPLIES_PER_DAY:
        print("[Max replies => skip comment_1_tweet]")
        return

    if not can_do_action(reply_actions, MAX_REPLIES_PER_DAY, replies_made):
        print("[Short-term or daily limit => skip comment]")
        return

    queries = []
    q_acc = build_query_accounts()
    if q_acc:
        queries.append(q_acc)
    q_hash = build_query_hashtags()
    if q_hash:
        queries.append(q_hash)

    for q in queries:
        try:
            resp = client_twitter.search_recent_tweets(
                query=q,
                max_results=15,
                tweet_fields=["created_at"],
            )
            if not resp.data:
                continue
            for tw in resp.data:
                if db.is_tweet_seen(conn, tw.id):
                    continue
                if not is_recent_enough(tw.created_at, hours=24):
                    continue
                if contains_insult(tw.text):
                    db.mark_tweet_seen(conn, tw.id)
                    continue
                # On commente
                prompt = (
                    f"User posted:\n{tw.text}\n"
                    "Write a short but reflective comment (<280 chars), referencing $Galaxy if relevant."
                )
                cmt = ask_openai(prompt)
                if cmt and len(cmt)<=280:
                    try:
                        client_twitter.create_tweet(
                            text=cmt,
                            in_reply_to_tweet_id=tw.id
                        )
                        replies_made += 1
                        record_action(reply_actions)
                        db.mark_tweet_seen(conn, tw.id)
                        print(f"[{datetime.now()}] Commented => {tw.id}")
                        return
                    except Exception as e:
                        print("Error commenting =>", e)
                else:
                    db.mark_tweet_seen(conn, tw.id)
        except Exception as e:
            print("Error in comment_1_tweet =>", e)

def respond_or_comment():
    """
    1) Tente respond_1_mention()
    2) Sinon comment_1_tweet()
    """
    responded = respond_1_mention()
    if not responded:
        comment_1_tweet()

def like_2_tweets():
    """
    Like 2 tweets
    """
    global likes_made
    if likes_made >= MAX_LIKES_PER_DAY:
        print("[Daily like limit => skip like_2_tweets]")
        return

    # On cherche sur accounts, fallback hashtags
    queries = []
    q_acc = build_query_accounts()
    if q_acc:
        queries.append(q_acc)
    q_hash = build_query_hashtags()
    if q_hash:
        queries.append(q_hash)

    count_liked = 0
    for q in queries:
        if count_liked >= 2:
            break
        try:
            resp = client_twitter.search_recent_tweets(
                query=q,
                max_results=20,
                tweet_fields=["created_at"]
            )
            if not resp.data:
                continue
            for tw in resp.data:
                if count_liked>=2:
                    break
                if db.is_tweet_seen(conn, tw.id):
                    continue
                if not is_recent_enough(tw.created_at, hours=24):
                    continue
                if not can_do_action(like_actions, MAX_LIKES_PER_DAY, likes_made):
                    return
                # On like
                try:
                    client_twitter.like(tw.id)
                    likes_made += 1
                    record_action(like_actions)
                    db.mark_tweet_seen(conn, tw.id)
                    count_liked += 1
                    print(f"[{datetime.now()}] Liked => {tw.id}")
                except Exception as e:
                    print("Error liking =>", e)
        except Exception as e:
            print("Error like_2_tweets =>", e)

def retweet_1_tweet():
    """
    Retweet 1
    """
    global retweets_made
    if not can_do_action(retweet_actions, MAX_RETWEETS_PER_DAY, retweets_made):
        print("[Daily retweet limit => skip retweet_1_tweet]")
        return

    queries = []
    q_acc = build_query_accounts()
    if q_acc:
        queries.append(q_acc)
    q_hash = build_query_hashtags()
    if q_hash:
        queries.append(q_hash)

    for q in queries:
        try:
            resp = client_twitter.search_recent_tweets(
                query=q,
                max_results=20,
                tweet_fields=["created_at"]
            )
            if not resp.data:
                continue
            for tw in resp.data:
                if db.is_tweet_seen(conn, tw.id):
                    continue
                if not is_recent_enough(tw.created_at, hours=24):
                    continue
                if contains_insult(tw.text):
                    db.mark_tweet_seen(conn, tw.id)
                    continue
                if not can_do_action(retweet_actions, MAX_RETWEETS_PER_DAY, retweets_made):
                    return
                try:
                    client_twitter.retweet(tw.id)
                    retweets_made += 1
                    record_action(retweet_actions)
                    db.mark_tweet_seen(conn, tw.id)
                    print(f"[{datetime.now()}] Retweeted => {tw.id}")
                    return
                except Exception as e:
                    print("Error retweet =>", e)
        except Exception as e:
            print("Error retweet_1_tweet =>", e)

def post_1_tweet():
    """
    Poster un tweet
    """
    global posts_made
    if not can_do_action(post_actions, MAX_POSTS_PER_DAY, posts_made):
        print("[Daily post limit => skip post_1_tweet]")
        return

    prompt = (
        "Generate a short but thoughtful tweet (<280 chars) about AI/Crypto, "
        "mention $Galaxy if relevant, end with a question."
    )
    txt = ask_openai(prompt)
    if not txt:
        return
    if len(txt)>280:
        txt = txt[:280]

    try:
        client_twitter.create_tweet(text=txt)
        posts_made += 1
        record_action(post_actions)
        print(f"[{datetime.now()}] Posted => {txt}")
    except Exception as e:
        print("Error posting =>", e)

##################################################
# BIG SEQUENCE
##################################################

def big_sequence():
    """
    5 sous-actions, 15s entre chacune, 
    puis 5min pause, recommence en boucle:

      1) respond_or_comment()
      2) respond_or_comment()
      3) like_2_tweets()
      4) retweet_1_tweet()
      5) post_1_tweet()
    """
    print(f"\n[{datetime.now()}] START big_sequence")

    # 1
    respond_or_comment()
    pytime.sleep(INTERVAL_BETWEEN_SUBACTIONS)

    # 2
    respond_or_comment()
    pytime.sleep(INTERVAL_BETWEEN_SUBACTIONS)

    # 3
    like_2_tweets()
    pytime.sleep(INTERVAL_BETWEEN_SUBACTIONS)

    # 4
    retweet_1_tweet()
    pytime.sleep(INTERVAL_BETWEEN_SUBACTIONS)

    # 5
    post_1_tweet()
    pytime.sleep(INTERVAL_BETWEEN_SUBACTIONS)

    print(f"[{datetime.now()}] => 5 sub-actions done. Pause 5 min => next cycle.")
    pytime.sleep(PAUSE_AFTER_CYCLE)
    print(f"[{datetime.now()}] END big_sequence => restarting cycle now...")

##################################################
# MAIN
##################################################

if __name__ == "__main__":
    print(f"[{datetime.now()}] Starting Twitter Bot - short intervals (15s) + final 5min pause per cycle.\n")
    while True:
        big_sequence()

    db.close_db(conn)
