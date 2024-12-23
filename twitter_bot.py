##############################################
# twitter_bot.py
##############################################

import os
import time
from datetime import datetime, timezone
from dotenv import load_dotenv
import tweepy
import time as pytime
import sqlite3

import db  # votre db.py => is_tweet_seen, mark_tweet_seen, init_db

##############################################
# CONFIG
##############################################

INTERVAL_BETWEEN_SUBACTIONS = 15   # 15 secondes entre chaque sous-action
PAUSE_AFTER_CYCLE = 300            # 5 minutes après le 8ᵉ

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
    "You are Sam Cooper, an official student at $Galaxy, one of the 3333 chosen to build a new future among the stars. "
    "You discuss crypto, AI, AI agents, NFTs, memecoins, and you reference $Galaxy if relevant. "
    "You are polite, no insults, respond in English. The goal is to grow the account."
)

# Vos hashtags et comptes cibles
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

MIN_FOLLOWERS = 100  # compte >=100 followers
MIN_LIKES = 100      # post >=100 likes
MENTION_MAX_HOURS = 4

##############################################
# INIT .env / Tweepy / OpenAI / DB
##############################################

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

# Bot ID (optionnel)
bot_username = "sam_cooper_nft"
try:
    user_data = client_twitter.get_user(username=bot_username)
    if user_data and user_data.data:
        bot_user_id = user_data.data.id
    else:
        bot_user_id = None
        print("Unable to retrieve bot user data.")
except Exception as e:
    print("Error retrieving bot user ID:", e)
    bot_user_id = None

conn = db.init_db()

##############################################
# HELPER: OPENAI
##############################################

def ask_openai(prompt, max_tokens=120, temperature=0.7):
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

##############################################
# LIMITS & DB
##############################################

def can_do_action(action_list, max_per_day, daily_count,
                  limit_short=SHORT_TERM_LIMIT, window=SHORT_TERM_WINDOW):
    if daily_count >= max_per_day:
        return False
    now_ts = pytime.time()
    # remove old
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


##############################################
# UTILS
##############################################

def is_recent_enough(created_at, hours=24):
    if not created_at:
        return False
    now_utc = datetime.now(timezone.utc)
    delta = now_utc - created_at
    return delta.total_seconds() < hours*3600

def contains_insult(text):
    insults = ["con","connard","idiot","abruti","merde","fdp","pute"]
    return any(i in text.lower() for i in insults)


##############################################
# MERGED SEARCH
##############################################

def search_tweets_merged():
    """
    Récupère 20 tweets depuis comptes cibles + 20 tweets depuis hashtags,
    Combine => trié par like_count desc,
    Filtre => author >=100 followers, post >=100 likes, pas seen, <24h, no insult
    Retourne la liste triée
    """
    results = []
    # 1) query accounts
    query_acc = None
    if TARGET_ACCOUNTS:
        acc_list = " OR ".join([f"from:{acc.replace('@','')}" for acc in TARGET_ACCOUNTS])
        query_acc = acc_list + " -is:retweet lang:en"

    # 2) query hashtags
    query_hash = None
    if TARGET_HASHTAGS:
        h_list = " OR ".join(TARGET_HASHTAGS)
        query_hash = h_list + " -is:retweet lang:en"

    try:
        if query_acc:
            ra = client_twitter.search_recent_tweets(
                query=query_acc,
                max_results=20,
                expansions=["author_id"],
                tweet_fields=["created_at","public_metrics"],
                user_fields=["public_metrics","username"]
            )
            if ra and ra.data:
                results.append(ra)  # on garde la resp

        if query_hash:
            rh = client_twitter.search_recent_tweets(
                query=query_hash,
                max_results=20,
                expansions=["author_id"],
                tweet_fields=["created_at","public_metrics"],
                user_fields=["public_metrics","username"]
            )
            if rh and rh.data:
                results.append(rh)

        # Combine
        merged_tweets = []
        for resp in results:
            user_map = {}
            if resp.includes and "users" in resp.includes:
                for u in resp.includes["users"]:
                    user_map[u.id] = u

            for t in resp.data:
                if db.is_tweet_seen(conn, t.id):
                    continue
                if not is_recent_enough(t.created_at, 24):
                    continue
                if contains_insult(t.text):
                    db.mark_tweet_seen(conn, t.id)
                    continue
                # check pm
                pm = getattr(t, "public_metrics", None)
                if not pm or pm["like_count"] < MIN_LIKES:
                    continue
                # check user
                u_obj = user_map.get(t.author_id, None)
                if not u_obj or not getattr(u_obj, "public_metrics", None):
                    continue
                if u_obj.public_metrics["followers_count"] < MIN_FOLLOWERS:
                    continue

                merged_tweets.append((t, pm["like_count"]))

        # Tri par like_count desc
        merged_tweets.sort(key=lambda x: x[1], reverse=True)

        # On retourne la liste de t pour usage
        return [x[0] for x in merged_tweets]

    except Exception as e:
        print("Error in search_tweets_merged =>", e)
        return []

##############################################
# SUB-ACTIONS
##############################################

def comment_1_tweet():
    """Commenter 1 tweet pertinent (≥100 likes, author≥100followers)."""
    global replies_made
    if replies_made >= MAX_REPLIES_PER_DAY:
        print("[Max replies => skip comment_1_tweet]")
        return
    if not can_do_action(reply_actions, MAX_REPLIES_PER_DAY, replies_made):
        print("[Rate limit => skip comment]")
        return

    tweet_list = search_tweets_merged()
    # On prend le premier s'il existe
    for tw in tweet_list:
        try:
            prompt = (
                f"User posted:\n{tw.text}\n"
                "Write a short reflective comment <280 chars, referencing $Galaxy if relevant."
            )
            cmt = ask_openai(prompt)
            if cmt and len(cmt)<=280:
                client_twitter.create_tweet(text=cmt, in_reply_to_tweet_id=tw.id)
                replies_made += 1
                record_action(reply_actions)
                db.mark_tweet_seen(conn, tw.id)
                print(f"[{datetime.now()}] Commented => {tw.id}")
                return
            else:
                db.mark_tweet_seen(conn, tw.id)
        except Exception as e:
            print("Error comment =>", e)
    # if none => skip

def respond_1_mention():
    """Répondre si mention <4h"""
    global replies_made
    if replies_made >= MAX_REPLIES_PER_DAY:
        return False
    if not bot_user_id:
        return False
    try:
        resp = client_twitter.get_users_mentions(
            id=bot_user_id,
            max_results=5,
            expansions=["author_id"],
            tweet_fields=["created_at"],
            user_fields=["username"]
        )
        if not resp.data:
            return False
        user_map = {}
        if resp.includes and "users" in resp.includes:
            for u in resp.includes["users"]:
                user_map[u.id] = u

        for mention in resp.data:
            if db.is_tweet_seen(conn, mention.id):
                continue
            if not is_recent_enough(mention.created_at, MENTION_MAX_HOURS):
                continue
            if not can_do_action(reply_actions, MAX_REPLIES_PER_DAY, replies_made):
                return False
            if contains_insult(mention.text):
                db.mark_tweet_seen(conn, mention.id)
                continue
            # ChatGPT
            auth_username = ""
            if mention.author_id in user_map:
                auth_username = user_map[mention.author_id].username

            prompt = (
                f"User mentioned us:\n{mention.text}\n"
                "Write a short reflective reply (<280 chars), referencing $Galaxy if relevant."
            )
            r_txt = ask_openai(prompt)
            if r_txt and len(r_txt)<=280:
                try:
                    final_txt = r_txt
                    if auth_username:
                        final_txt = f"@{auth_username} {r_txt}"
                    client_twitter.create_tweet(
                        text=final_txt,
                        in_reply_to_tweet_id=mention.id
                    )
                    replies_made += 1
                    record_action(reply_actions)
                    db.mark_tweet_seen(conn, mention.id)
                    print(f"[{datetime.now()}] Replied mention => {mention.id}")
                    return True
                except Exception as e:
                    print("Error replying =>", e)
            else:
                db.mark_tweet_seen(conn, mention.id)
    except Exception as e:
        print("Error respond_1_mention =>", e)
    return False


def like_1_tweet():
    """like 1 tweet pertinent."""
    global likes_made
    if likes_made >= MAX_LIKES_PER_DAY:
        print("[like_1_tweet => daily limit reached]")
        return
    if not can_do_action(like_actions, MAX_LIKES_PER_DAY, likes_made):
        print("[like_1_tweet => short-term limit => skip]")
        return

    tweet_list = search_tweets_merged()
    for tw in tweet_list:
        try:
            client_twitter.like(tw.id)
            likes_made += 1
            record_action(like_actions)
            db.mark_tweet_seen(conn, tw.id)
            print(f"[{datetime.now()}] Liked => {tw.id}")
            return
        except Exception as e:
            print("Error liking =>", e)
    # no tweets => skip

def retweet_1_tweet():
    """Retweet 1 tweet pertinent."""
    global retweets_made
    if not can_do_action(retweet_actions, MAX_RETWEETS_PER_DAY, retweets_made):
        print("[retweet_1_tweet => daily or short-term limit => skip]")
        return

    tweet_list = search_tweets_merged()
    for tw in tweet_list:
        try:
            client_twitter.retweet(tw.id)
            retweets_made += 1
            record_action(retweet_actions)
            db.mark_tweet_seen(conn, tw.id)
            print(f"[{datetime.now()}] Retweeted => {tw.id}")
            return
        except Exception as e:
            print("Error retweet =>", e)
    # none => skip

def post_1_tweet():
    """Poster un tweet 'breaking news' sur AI/Crypto/etc."""
    global posts_made
    if not can_do_action(post_actions, MAX_POSTS_PER_DAY, posts_made):
        print("[post_1_tweet => daily limit => skip]")
        return

    prompt = (
        "You are to post an ultra-pertinent Breaking News tweet about AI or Crypto, with possible regulations, "
        "economic or political aspects. Possibly mention $Galaxy. <280 chars, end with a question. "
        "Pretend you have the latest hot news from the internet."
    )
    txt = ask_openai(prompt, max_tokens=120)
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


##############################################
# BIG SEQUENCE (8 sub-actions)
##############################################

def big_sequence():
    """
    1) comment_1_tweet()
    2) comment_1_tweet()
    3) like_1_tweet()
    4) respond_1_mention()
    5) like_1_tweet()
    6) respond_1_mention()
    7) retweet_1_tweet()
    8) post_1_tweet()

    => 15s between each => total ~2min
    => then 5min pause => total ~7min cycle
    => loop
    """
    print(f"\n[{datetime.now()}] START big_sequence")

    # 1
    comment_1_tweet()
    pytime.sleep(INTERVAL_BETWEEN_SUBACTIONS)

    # 2
    comment_1_tweet()
    pytime.sleep(INTERVAL_BETWEEN_SUBACTIONS)

    # 3
    like_1_tweet()
    pytime.sleep(INTERVAL_BETWEEN_SUBACTIONS)

    # 4
    respond_1_mention()
    pytime.sleep(INTERVAL_BETWEEN_SUBACTIONS)

    # 5
    like_1_tweet()
    pytime.sleep(INTERVAL_BETWEEN_SUBACTIONS)

    # 6
    respond_1_mention()
    pytime.sleep(INTERVAL_BETWEEN_SUBACTIONS)

    # 7
    retweet_1_tweet()
    pytime.sleep(INTERVAL_BETWEEN_SUBACTIONS)

    # 8
    post_1_tweet()
    pytime.sleep(INTERVAL_BETWEEN_SUBACTIONS)

    print(f"[{datetime.now()}] => 8 sub-actions done, now pause 5min.")
    pytime.sleep(PAUSE_AFTER_CYCLE)
    print(f"[{datetime.now()}] END big_sequence => repeat.")

##############################################
# MAIN
##############################################

if __name__ == "__main__":
    print(f"[{datetime.now()}] Starting BOT with 8 subactions (2 comments, 2 likes, 2 respond, 1 retweet, 1 post), 15s intervals, 5min final pause.\n")
    while True:
        big_sequence()

    db.close_db(conn)
