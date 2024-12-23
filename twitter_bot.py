import os
import schedule
import time
from datetime import datetime, timezone, timedelta
from dotenv import load_dotenv
import tweepy
import time as pytime
import sqlite3

import db  # votre db.py dans le même dossier

###################################
# CONFIG
###################################

INTERVAL_BETWEEN_SUBACTIONS = 1200  # 20 min

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
    "and provide valuable insights. You subtly reference $Galaxy, you are polite, no insults, "
    "and by default you respond in English. You can also learn about new relevant accounts in AI/Crypto."
)

# Comptes cibles initiaux
TARGET_ACCOUNTS = [
    "@jeffy_eth","@0xzerebro","@solana","@base","@StargateFinance",
    "@truth_terminal","@YumaGroup","@DCGco","@virtuals_io","@luna_virtuals",
    "@CreatorBid","@ai16z","@punk3700","@shawmakesmagic","@galaxyuniwtf"
]
# Hashtags cibles
TARGET_HASHTAGS = ["#AI","#Crypto","#NFT","#AIAgents","#Bitcoin","#Galaxy"]

# On stockera ici des comptes “élargis” découverts
RELEVANT_ACCOUNTS = set()

MIN_FOLLOWERS = 300
MIN_LIKES = 100

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

# Récup ID du bot
bot_username = "sam_cooper_nft"
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
# HELPER : OPENAI
###################################

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

###################################
# LIMITS & DB
###################################

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

    # On peut aussi reset RELEVANT_ACCOUNTS si on veut
    RELEVANT_ACCOUNTS.clear()

    print(f"[{datetime.now()}] Daily counters have been reset.")

def is_recent_enough(created_at, hours=24):
    if not created_at:
        return False
    now_utc = datetime.now(timezone.utc)
    delta = now_utc - created_at
    return (delta.total_seconds() < hours*3600)

def contains_insult(text):
    insults = ["con","connard","idiot","abruti","merde","fdp","pute"]
    lower = text.lower()
    for w in insults:
        if w in lower:
            return True
    return False

###################################
# SCAN TWEETS HELPER
###################################

def build_query_accounts():
    # On combine TARGET_ACCOUNTS + RELEVANT_ACCOUNTS
    all_accounts = list(TARGET_ACCOUNTS) + list(RELEVANT_ACCOUNTS)
    if not all_accounts:
        return None
    q = " OR ".join([f"from:{acc.replace('@','')}" for acc in all_accounts])
    q += " -is:retweet lang:en"
    return q

def build_query_hashtags():
    if not TARGET_HASHTAGS:
        return None
    h = " OR ".join(TARGET_HASHTAGS)
    return h + " -is:retweet lang:en"

def check_and_add_relevant_account(user_obj):
    """
    Si user_obj a bcp de followers, ChatGPT peut décider si on l'ajoute à RELEVANT_ACCOUNTS
    """
    if not getattr(user_obj, "public_metrics", None):
        return
    if user_obj.public_metrics["followers_count"] < 3000:
        return
    # On peut demander à ChatGPT s'il est pertinent de l'ajouter
    prompt = f"""
    We found a user with {user_obj.public_metrics['followers_count']} followers, username {user_obj.username}.
    Are they interesting for AI/Crypto context? Respond 'yes' or 'no'.
    """
    dec = ask_openai(prompt, max_tokens=10)
    if dec and "yes" in dec.lower():
        RELEVANT_ACCOUNTS.add(f"@{user_obj.username}")

###################################
# RESPOND OR COMMENT
###################################

def respond_or_comment():
    """
    1) Tente de répondre à 1 mention <4h
    2) sinon commente un tweet
    """
    responded = respond_1_mention()
    if not responded:
        comment_1_tweet()

def respond_1_mention():
    """
    Répond 1 mention <4h
    """
    global replies_made
    if replies_made >= MAX_REPLIES_PER_DAY:
        return False

    try:
        resp = client_twitter.get_users_mentions(
            id=bot_user_id,
            max_results=5,
            tweet_fields=["created_at"],
            expansions=["author_id"],
            user_fields=["username","public_metrics"]
        )
        if not resp or not resp.data:
            return False
        user_map = {}
        if resp.includes and "users" in resp.includes:
            for u in resp.includes["users"]:
                user_map[u.id] = u

        for mention in resp.data:
            if db.is_tweet_seen(conn, mention.id):
                continue
            # On veut mention <4h
            if not is_recent_enough(mention.created_at, hours=4):
                continue
            if not can_do_action(reply_actions, MAX_REPLIES_PER_DAY, replies_made):
                return False
            if contains_insult(mention.text):
                db.mark_tweet_seen(conn, mention.id)
                continue

            author_username = user_map[mention.author_id].username if mention.author_id in user_map else ""
            # ChatGPT
            mention_prompt = (
                f"User mentioned us:\n{mention.text}\n"
                "Write a short but deeper answer in English (<280 chars), referencing $Galaxy if relevant."
            )
            reply_txt = ask_openai(mention_prompt, max_tokens=80)
            if reply_txt and len(reply_txt)<=280:
                try:
                    final_text = (f"@{author_username} {reply_txt}" if author_username else reply_txt)
                    client_twitter.create_tweet(text=final_text, in_reply_to_tweet_id=mention.id)
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
    """
    Commente 1 tweet => d'abord via accounts (TARGET+RELEVANT), puis fallback hashtags
    Filtre min 300 followers, 100 likes, etc.
    """
    global replies_made
    if replies_made >= MAX_REPLIES_PER_DAY:
        return

    if not can_do_action(reply_actions, MAX_REPLIES_PER_DAY, replies_made):
        return

    # 1) Rechercher via accounts
    queries = []
    q_acc = build_query_accounts()
    if q_acc:
        queries.append(q_acc)
    # 2) fallback => hashtags
    q_hash = build_query_hashtags()
    if q_hash:
        queries.append(q_hash)

    for q in queries:
        try:
            resp = client_twitter.search_recent_tweets(
                query=q,
                max_results=15,
                expansions=["author_id"],
                tweet_fields=["created_at","public_metrics"],
                user_fields=["username","public_metrics"]
            )
            if not resp or not resp.data:
                continue
            user_map = {}
            if resp.includes and "users" in resp.includes:
                for u in resp.includes["users"]:
                    user_map[u.id] = u

            for tw in resp.data:
                if db.is_tweet_seen(conn, tw.id):
                    continue
                if not is_recent_enough(tw.created_at, hours=24):
                    continue
                if contains_insult(tw.text):
                    db.mark_tweet_seen(conn, tw.id)
                    continue

                # Check min followers / min likes
                pm = getattr(tw, "public_metrics", None)
                if not pm or pm["like_count"]<MIN_LIKES:
                    continue
                if tw.author_id not in user_map:
                    continue
                u_obj = user_map[tw.author_id]
                if not getattr(u_obj, "public_metrics", None) or u_obj.public_metrics["followers_count"]<MIN_FOLLOWERS:
                    continue

                # On peut demander à ChatGPT s'il "approuve" de commenter => on skip ou comment
                decision_prompt = f"""
                We have a tweet from user {u_obj.username} with {u_obj.public_metrics['followers_count']} followers.
                The tweet says:
                {tw.text}

                Should we comment or skip? respond 'comment' or 'skip'
                """
                dec = ask_openai(decision_prompt, max_tokens=10)
                if not dec or "skip" in dec.lower():
                    db.mark_tweet_seen(conn, tw.id)
                    # On continue => on cherche un autre
                    continue

                # On commente => ChatGPT
                comment_prompt = f"""
                The user posted:
                {tw.text}
                Write a short but reflective comment in English (<280 chars), referencing $Galaxy if relevant.
                Provide some insight about AI/Crypto, 
                maybe mention a futuristic angle.
                """
                reply_txt = ask_openai(comment_prompt, max_tokens=80)
                if reply_txt and len(reply_txt)<=280:
                    try:
                        client_twitter.create_tweet(text=reply_txt, in_reply_to_tweet_id=tw.id)
                        replies_made += 1
                        record_action(reply_actions)
                        db.mark_tweet_seen(conn, tw.id)
                        print(f"[{datetime.now()}] Commented => {tw.id}")
                        # On check si on peut add user to relevant
                        check_and_add_relevant_account(u_obj)
                        return
                    except Exception as e:
                        print("Error commenting =>", e)
                else:
                    db.mark_tweet_seen(conn, tw.id)
        except Exception as e:
            print("Error in comment_1_tweet =>", e)
    # si rien => skip

###################################
# LIKE 2 TWEETS
###################################

def like_2_tweets():
    global likes_made
    if likes_made >= MAX_LIKES_PER_DAY:
        print("[Daily like limit reached] => skip like_2_tweets.")
        return

    # On fait la même logique : accounts, puis fallback hashtags
    # On like 2 max
    to_like = 2
    queries = []
    q_acc = build_query_accounts()
    if q_acc:
        queries.append(q_acc)
    q_hash = build_query_hashtags()
    if q_hash:
        queries.append(q_hash)

    liked_count = 0
    for q in queries:
        if liked_count>=to_like:
            break
        try:
            resp = client_twitter.search_recent_tweets(
                query=q,
                max_results=20,
                expansions=["author_id"],
                tweet_fields=["public_metrics","created_at"],
                user_fields=["public_metrics","username"]
            )
            if not resp.data:
                continue
            user_map = {}
            if resp.includes and "users" in resp.includes:
                for u in resp.includes["users"]:
                    user_map[u.id] = u

            for tw in resp.data:
                if liked_count>=to_like:
                    break
                if db.is_tweet_seen(conn, tw.id):
                    continue
                if not is_recent_enough(tw.created_at, hours=24):
                    continue

                pm = tw.public_metrics
                if not pm or pm["like_count"]<MIN_LIKES:
                    continue
                u_obj = user_map.get(tw.author_id, None)
                if not u_obj or u_obj.public_metrics["followers_count"]<MIN_FOLLOWERS:
                    continue

                if not can_do_action(like_actions, MAX_LIKES_PER_DAY, likes_made):
                    print("[Rate limit] can't like => skip.")
                    return

                # On like
                try:
                    client_twitter.like(tw.id)
                    likes_made += 1
                    record_action(like_actions)
                    db.mark_tweet_seen(conn, tw.id)
                    liked_count += 1
                    print(f"[{datetime.now()}] Liked => {tw.id}")
                    check_and_add_relevant_account(u_obj)
                except Exception as e:
                    print("Error liking =>", e)

        except Exception as e:
            print("Error like_2_tweets =>", e)

###################################
# RETWEET
###################################

def retweet_1_tweet():
    global retweets_made
    if not can_do_action(retweet_actions, MAX_RETWEETS_PER_DAY, retweets_made):
        print("[Retweet daily limit] => skip.")
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
                expansions=["author_id"],
                tweet_fields=["public_metrics","created_at"],
                user_fields=["public_metrics","username"]
            )
            if not resp.data:
                continue
            user_map = {}
            if resp.includes and "users" in resp.includes:
                for u in resp.includes["users"]:
                    user_map[u.id] = u

            for tw in resp.data:
                if db.is_tweet_seen(conn, tw.id):
                    continue
                if not is_recent_enough(tw.created_at, hours=24):
                    continue
                pm = tw.public_metrics
                if not pm or pm["like_count"]<MIN_LIKES:
                    continue
                u_obj = user_map.get(tw.author_id, None)
                if not u_obj or u_obj.public_metrics["followers_count"]<MIN_FOLLOWERS:
                    continue
                if contains_insult(tw.text):
                    db.mark_tweet_seen(conn, tw.id)
                    continue

                if not can_do_action(retweet_actions, MAX_RETWEETS_PER_DAY, retweets_made):
                    print("[Rate limit retweet] => skip.")
                    return

                try:
                    client_twitter.retweet(tw.id)
                    retweets_made += 1
                    record_action(retweet_actions)
                    db.mark_tweet_seen(conn, tw.id)
                    print(f"[{datetime.now()}] Retweeted => {tw.id}")
                    check_and_add_relevant_account(u_obj)
                    return
                except Exception as e:
                    print("Error retweet =>", e)
        except Exception as e:
            print("Error retweet_1_tweet =>", e)

###################################
# POST TWEET
###################################

def post_1_tweet():
    global posts_made
    if not can_do_action(post_actions, MAX_POSTS_PER_DAY, posts_made):
        print("[Daily post limit] => skip post.")
        return

    prompt = """
    Generate a thoughtful tweet (<280 chars) about AI/Crypto, referencing $Galaxy if relevant, 
    possibly mention a new event or futuristic angle. 
    End with a question.
    """
    tweet_txt = ask_openai(prompt, max_tokens=80)
    if not tweet_txt:
        return
    if len(tweet_txt)>280:
        tweet_txt = tweet_txt[:280]

    try:
        client_twitter.create_tweet(text=tweet_txt)
        posts_made += 1
        record_action(post_actions)
        print(f"[{datetime.now()}] Posted => {tweet_txt}")
    except Exception as e:
        print("Error posting =>", e)

###################################
# BIG SEQUENCE => 5 ACTIONS
###################################

def big_sequence():
    """
    1) respond_or_comment()
    2) respond_or_comment()
    3) like_2_tweets()
    4) retweet_1_tweet()
    5) post_1_tweet()
    20 min entre chaque, puis on loop.

    => 1 cycle = 5 x 20 min = 100 min
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

    print(f"[{datetime.now()}] END big_sequence => loop again")


###################################
# MAIN LOOP or SCHEDULER
###################################

def run_infinite_loop():
    while True:
        big_sequence()
        # on repart direct => en ~1h40 le cycle se refait

if __name__ == "__main__":
    print(f"[{datetime.now()}] Starting Twitter bot with 5-step subactions every 20 min.")
    # On peut reset counters minuit
    schedule.every().day.at("00:00").do(reset_counters)

    # Lancement direct
    while True:
        big_sequence()
        # => 5 actions x 20 min => 100 min => on recommence
        # ou: pass if you want manual re-lauch
        # On relance direct
    # si vous préférez, vous pouvez faire un scheduler event
    # run_infinite_loop()
    db.close_db(conn)
