import os
import schedule
import time
from datetime import datetime, timezone, timedelta
from dotenv import load_dotenv
import tweepy
import time as pytime
import sqlite3

import db  # votre db.py, dans le même dossier

###################################
# CONFIG
###################################

# On va définir le nombre de sous-actions dans la séquence big_sequence()
# => comment_2, retweet_1, like_2, respond_2, post_1 => total 9 sous-actions
# Entre chaque sous-action, on attend 15 minutes (900 secondes).
INTERVAL_BETWEEN_SUBACTIONS = 900  # 15 min en secondes

# On planifie de lancer big_sequence() toutes les 4h
SEQUENCE_EVERY_HOURS = 4

# Limites journalières (exemples)
MAX_POSTS_PER_DAY = 70
MAX_LIKES_PER_DAY = 40
MAX_REPLIES_PER_DAY = 40
MAX_RETWEETS_PER_DAY = 24

# Limite court terme
SHORT_TERM_LIMIT = 4
SHORT_TERM_WINDOW = 900  # 15 min en secondes

# Tracking
post_actions = []
like_actions = []
reply_actions = []
retweet_actions = []

posts_made = 0
likes_made = 0
replies_made = 0
retweets_made = 0

# ChatGPT Identity
BOT_IDENTITY = (
    "You are Sam Cooper, an official student at $Galaxy, one of the 3333 chosen to build "
    "a new future among the stars. You discuss crypto, AI, AI agents, NFTs, memecoins, and "
    "provide valuable insights. You subtly reference $Galaxy. You are polite, no insults, "
    "and by default respond in English."
)

###################################
# INIT .env / Tweepy / OpenAI / DB
###################################

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

bot_username = "sam_cooper_nft"  # adapt if needed
try:
    user_data = client_twitter.get_user(username=bot_username)
    if user_data and user_data.data:
        bot_user_id = user_data.data.id
    else:
        print("Unable to retrieve bot user data.")
        exit(1)
except Exception as e:
    print(f"Error retrieving bot user ID: {e}")
    exit(1)

conn = db.init_db()

###################################
# HELPERS
###################################

def ask_openai(prompt, max_tokens=120, temperature=0.7):
    """
    ChatGPT call with BOT_IDENTITY as system prompt.
    """
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
    """
    Check daily + short-term limit (4 actions/15min).
    """
    if daily_count >= max_per_day:
        return False
    now_ts = pytime.time()
    # remove actions older than 'window'
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

def contains_insult(text):
    # vous pouvez étoffer
    insults = ["con", "connard","idiot","abruti","merde","fdp","pute"]
    lower = text.lower()
    for w in insults:
        if w in lower:
            return True
    return False

def is_recent_enough(created_at, hours=24):
    """
    Check if the tweet/mention is < 'hours' old
    """
    if not created_at:
        return False
    # created_at est aware => compare à datetime.now(timezone.utc)
    delta = datetime.now(timezone.utc) - created_at
    return (delta.total_seconds() < hours*3600)

###################################
# SUB-ACTIONS
###################################

def comment_1_tweet():
    """
    Commente 1 tweet (analyse ~20 tweets),
    verifie <24h, can_do_action, etc.
    """
    global replies_made
    if replies_made >= MAX_REPLIES_PER_DAY:
        print(f"[{datetime.now()}] Reached daily reply limit => skip comment.")
        return

    query = "#Crypto OR #AI OR #NFT lang:en -is:retweet"
    try:
        resp = client_twitter.search_recent_tweets(
            query=query, 
            max_results=20,
            tweet_fields=["created_at"]
        )
        if not resp or not resp.data:
            print(f"[{datetime.now()}] No tweets found for comment => skip.")
            return
        
        for t in resp.data:
            if db.is_tweet_seen(conn, t.id):
                continue
            if not is_recent_enough(t.created_at, hours=24):
                continue
            if not can_do_action(reply_actions, MAX_REPLIES_PER_DAY, replies_made):
                print("[Rate limit] Can't comment now => skip.")
                return
            if contains_insult(t.text):
                db.mark_tweet_seen(conn, t.id)
                continue

            # ChatGPT prompt
            prompt = (
                f"User tweeted:\n{t.text}\n"
                "Write a short, relevant comment in English, <280 chars, referencing $Galaxy if suitable."
            )
            reply_txt = ask_openai(prompt, max_tokens=60)
            if reply_txt and len(reply_txt)<=280:
                try:
                    client_twitter.create_tweet(text=reply_txt, in_reply_to_tweet_id=t.id)
                    replies_made += 1
                    record_action(reply_actions)
                    db.mark_tweet_seen(conn, t.id)
                    print(f"[{datetime.now()}] Commented => {t.id}")
                    return
                except Exception as e:
                    print(f"[{datetime.now()}] Error commenting => {e}")
            # si on n'a pas pu commenter, on continue
    except Exception as e:
        print(f"[{datetime.now()}] Error in comment_1_tweet => {e}")

def retweet_1_tweet():
    """
    Retweeter 1 tweet
    """
    global retweets_made
    if not can_do_action(retweet_actions, MAX_RETWEETS_PER_DAY, retweets_made):
        print(f"[{datetime.now()}] Not allowed to retweet (limit).")
        return

    query = "#Crypto OR #AI OR #NFT lang:en -is:retweet"
    try:
        resp = client_twitter.search_recent_tweets(
            query=query,
            max_results=20,
            tweet_fields=["created_at"]
        )
        if not resp or not resp.data:
            print(f"[{datetime.now()}] No tweets found for retweet.")
            return
        for t in resp.data:
            if db.is_tweet_seen(conn, t.id):
                continue
            if not is_recent_enough(t.created_at, hours=24):
                continue
            if contains_insult(t.text):
                db.mark_tweet_seen(conn, t.id)
                continue
            try:
                client_twitter.retweet(t.id)
                retweets_made += 1
                record_action(retweet_actions)
                db.mark_tweet_seen(conn, t.id)
                print(f"[{datetime.now()}] Retweeted => {t.id}")
                return
            except Exception as e:
                print(f"[{datetime.now()}] Error retweeting => {e}")
    except Exception as e:
        print(f"[{datetime.now()}] retweet_1_tweet => {e}")

def like_1_tweet():
    """
    Liker 1 tweet
    """
    global likes_made
    if likes_made >= MAX_LIKES_PER_DAY:
        print(f"[{datetime.now()}] Daily like limit reached => skip.")
        return

    query = "#Crypto OR #AI OR #NFT lang:en -is:retweet"
    try:
        resp = client_twitter.search_recent_tweets(
            query=query,
            max_results=20,
            tweet_fields=["created_at"]
        )
        if not resp or not resp.data:
            print(f"[{datetime.now()}] No tweets found to like => skip.")
            return

        for t in resp.data:
            if db.is_tweet_seen(conn, t.id):
                continue
            if not is_recent_enough(t.created_at, hours=24):
                continue
            if not can_do_action(like_actions, MAX_LIKES_PER_DAY, likes_made):
                print("[Rate limit] Can't like now.")
                return
            if contains_insult(t.text):
                db.mark_tweet_seen(conn, t.id)
                continue
            try:
                client_twitter.like(t.id)
                likes_made += 1
                record_action(like_actions)
                db.mark_tweet_seen(conn, t.id)
                print(f"[{datetime.now()}] Liked => {t.id}")
                return
            except Exception as e:
                print(f"[{datetime.now()}] Error liking => {e}")
    except Exception as e:
        print(f"[{datetime.now()}] like_1_tweet => {e}")

def respond_1_mention():
    """
    Répondre à 1 mention
    """
    global replies_made
    if replies_made >= MAX_REPLIES_PER_DAY:
        print(f"[{datetime.now()}] Daily mention limit reached => skip.")
        return

    try:
        resp = client_twitter.get_users_mentions(
            id=bot_user_id,
            max_results=5,
            tweet_fields=["created_at"]
        )
        if not resp or not resp.data:
            print(f"[{datetime.now()}] No mentions found => skip.")
            return
        for mention in resp.data:
            if db.is_tweet_seen(conn, mention.id):
                continue
            if not is_recent_enough(mention.created_at, hours=24):
                continue
            if not can_do_action(reply_actions, MAX_REPLIES_PER_DAY, replies_made):
                print("[Rate limit] Can't respond now => skip.")
                return
            if contains_insult(mention.text):
                db.mark_tweet_seen(conn, mention.id)
                continue

            mention_prompt = (
                f"User mentioned us:\n{mention.text}\n"
                "Respond politely in English (<280 chars), referencing $Galaxy if relevant."
            )
            reply_txt = ask_openai(mention_prompt, max_tokens=60)
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
                    return
                except Exception as e:
                    print(f"[{datetime.now()}] Error replying mention => {e}")
    except Exception as e:
        print(f"[{datetime.now()}] respond_1_mention => {e}")

def post_1_tweet():
    """
    Poster 1 tweet normal
    """
    global posts_made
    if not can_do_action(post_actions, MAX_POSTS_PER_DAY, posts_made):
        print(f"[{datetime.now()}] Daily post limit => skip.")
        return

    prompt = (
        "Generate a short but thoughtful tweet (<280 chars) about AI/Crypto. Mention $Galaxy if relevant. End with a question."
    )
    txt = ask_openai(prompt, max_tokens=80)
    if not txt:
        print(f"[{datetime.now()}] No GPT response => skip post.")
        return

    if len(txt)>280:
        txt = txt[:280]

    try:
        client_twitter.create_tweet(text=txt)
        posts_made += 1
        record_action(post_actions)
        print(f"[{datetime.now()}] Posted => {txt}")
    except Exception as e:
        print(f"[{datetime.now()}] Error posting => {e}")

###################################
# BIG SEQUENCE (9 sous-actions)
###################################

def big_sequence():
    """
    1) comment_1_tweet -> wait 15min
    2) comment_1_tweet -> wait 15min
    3) retweet_1_tweet -> wait 15min
    4) like_1_tweet -> wait 15min
    5) like_1_tweet -> wait 15min
    6) respond_1_mention -> wait 15min
    7) respond_1_mention -> wait 15min
    8) post_1_tweet
    (Au total, 8 intervalles * 15min => 2h)
    """

    print(f"\n[{datetime.now()}] START big_sequence")

    # 1) commenter 1 tweet
    comment_1_tweet()
    pytime.sleep(INTERVAL_BETWEEN_SUBACTIONS)

    # 2) commenter 1 tweet
    comment_1_tweet()
    pytime.sleep(INTERVAL_BETWEEN_SUBACTIONS)

    # 3) retweeter 1 tweet
    retweet_1_tweet()
    pytime.sleep(INTERVAL_BETWEEN_SUBACTIONS)

    # 4) like_1_tweet
    like_1_tweet()
    pytime.sleep(INTERVAL_BETWEEN_SUBACTIONS)

    # 5) like_1_tweet
    like_1_tweet()
    pytime.sleep(INTERVAL_BETWEEN_SUBACTIONS)

    # 6) respond_1_mention
    respond_1_mention()
    pytime.sleep(INTERVAL_BETWEEN_SUBACTIONS)

    # 7) respond_1_mention
    respond_1_mention()
    pytime.sleep(INTERVAL_BETWEEN_SUBACTIONS)

    # 8) post_1_tweet
    post_1_tweet()

    print(f"[{datetime.now()}] END big_sequence (~2h)")

###################################
# SCHEDULE
###################################

def schedule_tasks():
    # reset minuit
    schedule.every().day.at("00:00").do(reset_counters)

    # On lance big_sequence() toutes les 4h
    # => par exemple : 00:00, 04:00, 08:00, 12:00, 16:00, 20:00
    schedule.every(SEQUENCE_EVERY_HOURS).hours.at(":00").do(big_sequence)

def run_scheduler():
    schedule_tasks()
    while True:
        schedule.run_pending()
        pytime.sleep(10)

###################################
# MAIN
###################################

if __name__ == "__main__":
    print(f"[{datetime.now()}] Bot started. Each big_sequence = ~2h, repeated every {SEQUENCE_EVERY_HOURS}h.")
    
    # Lancement immédiat
    big_sequence()

    # Puis on laisse le schedule relancer le sequence
    run_scheduler()

    db.close_db(conn)
