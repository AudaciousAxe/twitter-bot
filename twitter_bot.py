##############################################
# twitter_bot.py
##############################################
import os
import time
from datetime import datetime
import re
import sqlite3

import tweepy
import openai
from dotenv import load_dotenv

import db  # init_db si vous voulez un stockage, sinon inutile

##############################################
# CONFIG
##############################################

NEWS_WAIT = 60                 # 1 minute entre la news et le premier commentaire
COMMENT_WAIT = 60              # 1 minute entre les 2 commentaires
PAUSE_BETWEEN_CYCLES = 3600    # 1 heure d’attente après les 2 commentaires

MIN_FOLLOWERS = 50  # impose un minimum si vous le souhaitez

TARGET_ACCOUNTS = [
    '@galaxyuniwtf','@0xzerebro','@solana','@base','@StargateFinance',
    '@truth_terminal','@YumaGroup','@DCGco','@virtuals_io','@luna_virtuals',
    '@CreatorBid','@ai16z','@punk3700','@shawmakesmagic','@apecoin',
    '@markus9x','@ethmage','@aixbt_agent',
    '@cryptopunk7213','@ai16zdao','@Farokh','@saylor','@Bitcoin'
]

BOT_IDENTITY = (
    "You are Sam Cooper, an official student at $Galaxy, one of the 3333 chosen to build "
    "a new future among the stars. You discuss crypto, AI, AI agents, NFTs, memecoins. "
    "You are polite, no insults, respond in English. The goal is to grow the account."
)

##############################################
# ARTICLES
##############################################

ARTICLES_FILE = r"C:\Users\BotX\articles_today.txt"
articles_list = []
current_article_index = 0

##############################################
# GLOBALS
##############################################

comment_acc_idx = 0   # round-robin pour commenter
posts_made = 0

##############################################
# INIT
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
    print("Error: Missing env variables.")
    exit(1)

openai.api_key = OPENAI_API_KEY

client_twitter = tweepy.Client(
    bearer_token=TWITTER_BEARER_TOKEN,
    consumer_key=TWITTER_API_KEY,
    consumer_secret=TWITTER_API_SECRET_KEY,
    access_token=TWITTER_ACCESS_TOKEN,
    access_token_secret=TWITTER_ACCESS_TOKEN_SECRET
)

conn = db.init_db()

try:
    bot_username = "sam_cooper_nft"
    user_data = client_twitter.get_user(username=bot_username)
    if user_data and user_data.data:
        bot_user_id = user_data.data.id
    else:
        bot_user_id = None
except Exception as e:
    print("Error retrieving bot user ID:", e)
    bot_user_id = None

##############################################
# GPT HELPER
##############################################

def ask_openai(prompt, max_tokens=240, temperature=0.7):
    try:
        resp = openai.ChatCompletion.create(
            model="gpt-3.5-turbo",
            messages=[
                {"role":"system","content":BOT_IDENTITY},
                {"role":"user","content":prompt}
            ],
            max_tokens=max_tokens,
            temperature=temperature
        )
        return resp.choices[0].message.content.strip()
    except Exception as e:
        print("[ask_openai] Error =>", e)
        return None

##############################################
# LOAD ARTICLES
##############################################

def load_articles_from_file():
    arr=[]
    current={}
    try:
        with open(ARTICLES_FILE,"r",encoding="utf-8") as f:
            for line in f:
                line=line.strip()
                if not line:
                    continue
                if line.startswith("--"):
                    if current:
                        arr.append(current)
                    current={}
                    continue
                if line.startswith("ID:"):
                    current["id"]= line.replace("ID:","").strip()
                elif line.startswith("TITLE:"):
                    current["title"]= line.replace("TITLE:","").strip()
                elif line.startswith("DESC:"):
                    current["desc"]= line.replace("DESC:","").strip()
                elif line.startswith("LINK:"):
                    current["link"]= line.replace("LINK:","").strip()
        if current:
            arr.append(current)
    except Exception as e:
        print("[load_articles_from_file] =>", e)
    return arr

def init_articles():
    global articles_list, current_article_index
    articles_list= load_articles_from_file()
    current_article_index=0
    print(f"[INIT] => loaded {len(articles_list)} articles from file")

def get_next_article():
    global articles_list, current_article_index
    if not articles_list:
        return None
    if current_article_index>= len(articles_list):
        return None
    a= articles_list[current_article_index]
    current_article_index+=1
    return a

##############################################
# WAIT => get last tweet (no skip)
##############################################

def get_last_tweet_of_account(acc):
    """
    On récupère le dernier tweet du compte, même si déjà commenté
    """
    acc_clean= acc.replace("@","")
    while True:
        try:
            user_data= client_twitter.get_user(username=acc_clean,user_fields=["public_metrics"])
            if not user_data or not user_data.data:
                time.sleep(10)
                continue

            fc= user_data.data.public_metrics["followers_count"]
            if fc< MIN_FOLLOWERS:
                time.sleep(10)
                continue

            uid= user_data.data.id
            resp= client_twitter.get_users_tweets(
                id=uid,
                max_results=5,
                tweet_fields=["created_at","public_metrics","conversation_id"]
            )
            if resp and resp.data:
                # dernier tweet => resp.data[0]
                return resp.data[0]
            time.sleep(10)
        except Exception as e:
            if "429" in str(e):
                print("[429 => wait 15min]")
                time.sleep(900)
            else:
                print("[get_last_tweet_of_account] => error:", e)
                time.sleep(10)

##############################################
# 1) POST NEWS + SOURCE
##############################################

def post_news_and_source():
    global posts_made
    art= get_next_article()
    while not art:
        print("[post_news_and_source] => no article => wait10s")
        time.sleep(10)
        art= get_next_article()

    sid= art.get("id","")
    title= art.get("title","")
    desc= art.get("desc","")
    link= art.get("link","")

    prompt=(
        f"Source: {sid}\nHeadline:\n{title}\nDesc:\n{desc}\n"
        "Write a Breaking News tweet <280 chars, mention the @source if relevant, end with a question."
    )
    txt= ask_openai(prompt)
    if not txt:
        return

    final_tweet= txt.strip()
    if len(final_tweet)>280:
        final_tweet= final_tweet[:280]

    try:
        resp= client_twitter.create_tweet(text=final_tweet)
        posts_made+=1
        if resp and resp.data:
            tid= resp.data["id"]
            print(f"[{datetime.now()}] Posted => {final_tweet}")
            # reply => source
            src_txt= f"Source: {link}"
            client_twitter.create_tweet(text=src_txt,in_reply_to_tweet_id=tid)
            print(f"[{datetime.now()}] => Replied => {src_txt}")
    except Exception as e:
        print("[post_news_and_source] => error:", e)

##############################################
# 2) COMMENT A NEXT ACCOUNT (ROUND ROBIN)
##############################################

def comment_1_account():
    global comment_acc_idx
    acc= TARGET_ACCOUNTS[comment_acc_idx]
    comment_acc_idx= (comment_acc_idx+1)% len(TARGET_ACCOUNTS)

    tw= get_last_tweet_of_account(acc)
    prompt=(
        f"User (@{acc}) last tweet:\n{tw.text}\n"
        "Write a short reflective comment (<280 chars). End with a question."
    )
    cmt= ask_openai(prompt)
    if not cmt:
        return
    if len(cmt)>280:
        cmt= cmt[:280]

    try:
        client_twitter.create_tweet(text=cmt, in_reply_to_tweet_id=tw.id)
        print(f"[{datetime.now()}] Commented => {tw.id} (acc={acc})")
    except Exception as e:
        print("[comment_1_account] => error:", e)

##############################################
# MAIN CYCLE
##############################################

def main_cycle():
    print(f"\n[{datetime.now()}] START cycle => news + 2 comments, then 1h wait")

    # 1) post news + source
    post_news_and_source()

    # wait 1 min
    print(f"[Wait => {NEWS_WAIT}s]")
    time.sleep(NEWS_WAIT)

    # 2) comment => 1er compte
    comment_1_account()

    # wait 1 min
    print(f"[Wait => {COMMENT_WAIT}s]")
    time.sleep(COMMENT_WAIT)

    # 3) comment => 2ᵉ compte
    comment_1_account()

    # then wait 1h
    print(f"[Wait => {PAUSE_BETWEEN_CYCLES}s => 1h]")
    time.sleep(PAUSE_BETWEEN_CYCLES)

    print(f"[{datetime.now()}] END cycle => next cycle.")

##############################################
# MAIN
##############################################

if __name__=="__main__":
    print(f"[{datetime.now()}] => Start: news+source, then 2 comments (1min interval), then 1h.\n")

    init_articles()

    while True:
        main_cycle()

