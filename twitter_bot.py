########################################
# twitter_bot.py
########################################

import os
import schedule
import time
from datetime import datetime, timezone
from dotenv import load_dotenv
import tweepy
import time as pytime
import sqlite3

import db  # votre db.py si vous avez is_tweet_seen, mark_tweet_seen, etc.

########################################
# CONFIG
########################################

# Sous-actions :
#   1) respond_or_comment()
#   2) respond_or_comment()
#   3) like_2_tweets()
#   4) retweet_1_tweet()
#   5) post_1_tweet()
# Intervalle = 15 s entre chaque sous-action pour TEST
# + 5 min (300 s) de pause après la 5ᵉ
# => cycle ~6 min

INTERVAL_BETWEEN_SUBACTIONS = 15   # 15 secondes (pour test)
PAUSE_AFTER_5TH_ACTION = 300       # 5 minutes

MAX_POSTS_PER_DAY = 70
MAX_LIKES_PER_DAY = 40
MAX_REPLIES_PER_DAY = 40
MAX_RETWEETS_PER_DAY = 24

SHORT_TERM_LIMIT = 4
SHORT_TERM_WINDOW = 900  # 15 min

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
    "crypto opportunities, and philosophical reflections about AI in crypto. "
    "Your goal is to provide value and subtly encourage interest in @galaxyuniwtf and $Galaxy. "
    "You are optimistic, polite, no insults. By default, respond in English."
)

# **Hashtags** et **@** fournis initialement :
TARGET_HASHTAGS = [
    '#Crypto','#NFT','#IA','#GenerativeAI','#AIAgents','#Bittensor',
    '#zerebro','#ethermage','#Agents','#ElizaAI','#ai16z','#Bitcoin','#GalaxyNews'
]
TARGET_ACCOUNTS = [
    '@jeffy_eth','@0xzerebro','@solana','@base','@StargateFinance',
    '@truth_terminal','@YumaGroup','@DCGco','@virtuals_io','@luna_virtuals',
    '@CreatorBid','@ai16z','@punk3700','@shawmakesmagic','@apecoin',
    '@galaxyuniwtf'
]

MIN_FOLLOWERS = 300
MIN_LIKES = 100
MENTION_MAX_HOURS = 4

########################################
# INIT .env / Tweepy / OpenAI / DB
########################################

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

# Récup ID bot (optionnel)
bot_username = "sam_cooper_nft"
try:
    user_data = client_twitter.get_user(username=bot_username)
    if user_data and user_data.data:
        bot_user_id = user_data.data.id
    else:
        bot_user_id = None
        print("Unable to retrieve bot user data.")
except Exception as e:
    print(f"Error retrieving bot user ID: {e}")
    bot_user_id = None

conn = db.init_db()

########################################
# HELPERS
########################################

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

def is_recent_enough(created_at, hours=24):
    if not created_at:
        return False
    now_utc = datetime.now(timezone.utc)
    delta = now_utc - created_at
    return (delta.total_seconds() < hours*3600)

def contains_insult(text):
    insults = ["con","connard","idiot","abruti","merde","fdp","pute"]
    txt_lower = text.lower()
    return any(i in txt_lower for i in insults)

########################################
# BUILD QUERIES
########################################

def build_query_accounts():
    if not TARGET_ACCOUNTS:
        return None
    from_list = " OR ".join([f"from:{acc.replace('@','')}" for acc in TARGET_ACCOUNTS])
    return from_list + " -is:retweet lang:en"

def build_query_hashtags():
    if not TARGET_HASHTAGS:
        return None
    h_list = " OR ".join(TARGET_HASHTAGS)
    return h_list + " -is:retweet lang:en"

########################################
# ACTIONS
########################################

def respond_1_mention():
    """
    Tente de répondre 1 mention (<4h).
    """
    global replies_made
    if replies_made >= MAX_REPLIES_PER_DAY:
        return False
    if not bot_user_id:
        return False

    try:
        resp = client_twitter.get_users_mentions(
            id=bot_user_id,
            max_results=5,
            tweet_fields=["created_at"]
        )
        if not resp.data:
            return False
        for mention in resp.data:
            if db.is_tweet_seen(conn, mention.id):
                continue
            if not is_recent_enough(mention.created_at, hours=MENTION_MAX_HOURS):
                continue
            if not can_do_action(reply_actions, MAX_REPLIES_PER_DAY, replies_made):
                return False
            if contains_insult(mention.text):
                db.mark_tweet_seen(conn, mention.id)
                continue

            # ChatGPT
            prompt = (
                f"The user mentioned us:\n{mention.text}\n"
                "Write a short, reflective answer (<280 chars), referencing $Galaxy if relevant."
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
                    print(f"Error replying mention => {e}")
            else:
                db.mark_tweet_seen(conn, mention.id)
    except Exception as e:
        print("Error respond_1_mention =>", e)
    return False

def comment_1_tweet():
    global replies_made
    if replies_made >= MAX_REPLIES_PER_DAY:
        print("[Max replies => skip comment_1_tweet]")
        return
    if not can_do_action(reply_actions, MAX_REPLIES_PER_DAY, replies_made):
        print("[Limit => skip comment_1_tweet]")
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
                max_results=10,
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

                # Prompt
                prompt = (
                    f"User posted:\n{tw.text}\n"
                    "Write a short but reflective comment <280 chars, referencing $Galaxy if relevant."
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
    responded = respond_1_mention()
    if not responded:
        comment_1_tweet()

def like_2_tweets():
    global likes_made
    if likes_made >= MAX_LIKES_PER_DAY:
        print("[Daily like limit => skip like_2_tweets]")
        return
    queries = []
    q_acc = build_query_accounts()
    if q_acc:
        queries.append(q_acc)
    q_hash = build_query_hashtags()
    if q_hash:
        queries.append(q_hash)

    liked_count = 0
    for q in queries:
        if liked_count>=2:
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
                if liked_count>=2:
                    break
                if db.is_tweet_seen(conn, tw.id):
                    continue
                if not is_recent_enough(tw.created_at, hours=24):
                    continue
                if not can_do_action(like_actions, MAX_LIKES_PER_DAY, likes_made):
                    return
                # like
                try:
                    client_twitter.like(tw.id)
                    likes_made += 1
                    record_action(like_actions)
                    db.mark_tweet_seen(conn, tw.id)
                    liked_count += 1
                    print(f"[{datetime.now()}] Liked => {tw.id}")
                except Exception as e:
                    print("Error liking =>", e)
        except Exception as e:
            print("Error like_2_tweets =>", e)

def retweet_1_tweet():
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
                max_results=10,
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
    global posts_made
    if not can_do_action(post_actions, MAX_POSTS_PER_DAY, posts_made):
        print("[Daily post limit => skip post_1_tweet]")
        return
    prompt = (
        "Generate a short tweet (<280 chars) about AI/Crypto, referencing $Galaxy, end with a question."
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

########################################
# BIG SEQUENCE
########################################

def big_sequence():
    """
    1) respond_or_comment()
    2) respond_or_comment()
    3) like_2_tweets()
    4) retweet_1_tweet()
    5) post_1_tweet()

    => 15s entre chaque
    => puis 5min pause
    => on relance
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

    print(f"[{datetime.now()}] Done 5 actions => now 5min pause before new cycle.")
    pytime.sleep(PAUSE_AFTER_5TH_ACTION)
    print(f"[{datetime.now()}] End of cycle => looping again...")

########################################
# MAIN
########################################

if __name__ == "__main__":
    print(f"[{datetime.now()}] Starting BOT with 5-step subactions, 15s intervals, final 5min pause.\n")

    # Boucle infinie
    while True:
        big_sequence()

    db.close_db(conn)
