#!/usr/bin/env python3
import os
import sys
import random
import time
import re
import requests
import tweepy
import logging
from datetime import datetime
from typing import List, Optional
from dotenv import load_dotenv
from pytrends.request import TrendReq

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] [%(module)s] %(message)s',
    handlers=[
        logging.FileHandler('bot.log'),
        logging.StreamHandler()
    ]
)

load_dotenv()

class BotConfig:
    MODEL_ENDPOINTS = [
        "HuggingFaceH4/zephyr-7b-beta",
        "mistralai/Mixtral-8x7B-Instruct-v0.1",
        "google/gemma-7b-it"
    ]

    FALLBACK_TWEETS = [
        "🌍 {trend} matters! Join the discussion. #{trend_clean}",
        "💡 What's your take on {trend}? Let's talk. #{trend_clean}",
        "🤝 Align with {trend} advocates. Share your view. #{trend_clean}"
    ]

    REQUIRED_VARS = [
        "TWITTER_API_KEY", "TWITTER_API_SECRET",
        "TWITTER_ACCESS_TOKEN", "TWITTER_ACCESS_SECRET",
        "HUGGINGFACE_API_TOKEN"
    ]

    CONTENT_SAFETY = {
        "banned_phrases": [
            "blown away", "breaking news", "exposed", "leaked",
            "🚨", "😱", "💣", "🔥", "🚨", "💥", "ViralForChange",
            "Join me", "Check out", "Purchase", "Click here",
            "The tweet starts with"
        ],
        "allowed_emojis": ["📊", "🔍", "💡", "🌍", "💬", "🌟", "🚀"]
    }

    RATE_LIMIT_BACKOFF = 300  # 5 minutes
    MAX_RETRIES = 5

class TwitterClient:
    def __init__(self):
        self._validate_credentials()
        self.client = self._init_client()

    def _validate_credentials(self):
        missing = [var for var in BotConfig.REQUIRED_VARS if not os.getenv(var)]
        if missing:
            logging.critical(f"Missing credentials: {', '.join(missing)}")
            raise SystemExit(1)

    def _init_client(self):
        try:
            return tweepy.Client(
                consumer_key=os.getenv("TWITTER_API_KEY"),
                consumer_secret=os.getenv("TWITTER_API_SECRET"),
                access_token=os.getenv("TWITTER_ACCESS_TOKEN"),
                access_token_secret=os.getenv("TWITTER_ACCESS_SECRET"),
                wait_on_rate_limit=True
            )
        except Exception as e:
            logging.critical(f"Twitter client initialization failed: {e}")
            raise SystemExit(1)

    def post_tweet(self, text: str):
        attempt = 0
        while attempt < BotConfig.MAX_RETRIES:
            try:
                response = self.client.create_tweet(text=text)
                logging.info(f"Tweet posted: {response.data['id']}")
                return
            except tweepy.TooManyRequests as e:
                self._handle_rate_limit(e, attempt)
            except tweepy.Forbidden as e:
                logging.error(f"Content policy violation: {e.api_errors}")
                return
            except Exception as e:
                logging.error(f"Tweet failed (attempt {attempt+1}): {str(e)[:100]}")
                return
            finally:
                attempt += 1

    def _handle_rate_limit(self, error, attempt):
        reset = int(error.response.headers.get('x-rate-limit-reset', time.time() + 300))
        wait = max(reset - int(time.time()), 0)
        logging.warning(f"Rate limit hit (attempt {attempt+1}/{BotConfig.MAX_RETRIES}). "
                        f"Backing off for {wait}s...")
        time.sleep(wait)

class ContentGenerator:
    def __init__(self):
        self._validate_token()

    def _validate_token(self):
        if not os.getenv("HUGGINGFACE_API_TOKEN"):
            logging.critical("Missing HuggingFace API token")
            raise SystemExit(1)

    def generate(self, prompt: str) -> Optional[str]:
        headers = {
            "Authorization": f"Bearer {os.getenv('HUGGINGFACE_API_TOKEN')}",
            "User-Agent": "TwitterBot/2.0"
        }
        data = {
            "inputs": prompt,
            "parameters": {
                "max_new_tokens": 100,
                "temperature": 0.7,
                "top_p": 0.9,
                "repetition_penalty": 1.2,
                "return_full_text": False
            }
        }

        for model in random.sample(BotConfig.MODEL_ENDPOINTS, len(BotConfig.MODEL_ENDPOINTS)):
            try:
                response = requests.post(
                    f"https://api-inference.huggingface.co/models/{model}",
                    headers=headers,
                    json=data,
                    timeout=20
                )
                if response.status_code == 403:
                    logging.error(f"Access denied to model {model}. Skipping.")
                    continue
                response.raise_for_status()
                result = response.json()
                return result[0].get("generated_text", "").strip()
            except requests.exceptions.RequestException as e:
                logging.error(f"Model {model[:15]}... failed: {str(e)[:100]}")
            except Exception as e:
                logging.error(f"Unexpected error with {model}: {str(e)[:100]}")
        
        return None

class TrendAnalyzer:
    @staticmethod
    def get_trends(country: str = 'united_states') -> List[str]:  
        try:
            pytrends = TrendReq(hl='en-US', tz=360)
            df = pytrends.trending_searches(pn=country)
            trends = [
                t for t in df[0].tolist()
                if len(t) > 4 and not t.isdigit() and t.isalpha()
            ]
            return trends[:5] if trends else ["AI", "Climate", "Healthcare"]
        except Exception as e:
            logging.error(f"Trend retrieval failed: {e}")
            return ["AI", "Climate", "Healthcare"]

class ContentFormatter:
    @staticmethod
    def format_tweet(raw_text: str, trend: str) -> str:
        current_year = datetime.now().year
        clean_text = raw_text.replace("\n", " ").replace('"', '').strip()
        
        # Remove meta-text and unwanted phrases
        clean_text = re.sub(r'^The tweet starts with.*?\.\s*', '', clean_text, flags=re.IGNORECASE)
        for phrase in BotConfig.CONTENT_SAFETY["banned_phrases"]:
            clean_text = clean_text.replace(phrase, "")
        
        # URL removal (including pic.twitter.com)
        clean_text = re.sub(r'(https?://\S+|pic\.twitter\.com/\S+)', '', clean_text)
        
        # Year normalization
        clean_text = re.sub(r'\b(20\d{2})\b', str(current_year), clean_text)
        
        # Extract existing hashtags (and remove them)
        existing_tags = [word for word in clean_text.split() if word.startswith("#")]
        clean_text = re.sub(r'(\s+)(#(\w+))', '', clean_text)
        
        # Trend hashtags
        trend_clean = trend.replace(" ", "")
        hashtags = [f"#{trend_clean}", f"#{trend_clean}{current_year}"]
        
        # Merge and deduplicate hashtags (max 3)
        all_tags = hashtags + existing_tags
        all_tags = list(dict.fromkeys(all_tags))[:3]
        
        # Assemble final text with hashtags
        clean_text += " " + " ".join(all_tags)
        
        # Emoji safety & length enforcement
        clean_text = ''.join(
            c for c in clean_text 
            if c in BotConfig.CONTENT_SAFETY["allowed_emojis"] or c.isalnum() or c in ' #'
        )
        if len(clean_text) > 280:
            clean_text = clean_text[:250].rsplit(' ', 1)[0] + "…"
        
        return clean_text.strip()

def main():
    try:
        client = TwitterClient()
        generator = ContentGenerator()
        trends = TrendAnalyzer.get_trends()
        selected_trend = random.choice(trends)
        logging.info(f"Selected trend: {selected_trend}")
        
        # Improved prompt to avoid meta-responses
        prompt = (
            f"Write a tweet about '{selected_trend}' that starts with a bold question, "
            "includes 1-2 relevant hashtags, and ends with a call to engage. "
            "Keep it concise (under 200 characters)."
        )
        
        raw_content = generator.generate(prompt)
        if not raw_content:
            logging.warning("Content generation failed. Using fallback.")
            trend_clean = selected_trend.replace(" ", "")
            fallback = random.choice(BotConfig.FALLBACK_TWEETS).format(
                trend=selected_trend,
                trend_clean=trend_clean
            )
            client.post_tweet(fallback)
            return
        
        tweet_text = ContentFormatter.format_tweet(raw_content, selected_trend)
        logging.info(f"Final tweet: {tweet_text}")
        client.post_tweet(tweet_text)
        
    except KeyboardInterrupt:
        logging.info("Bot interrupted by user. Exiting...")
        sys.exit(0)
    except Exception as e:
        logging.critical(f"Critical failure: {str(e)[:100]}")
        sys.exit(1)

if __name__ == "__main__":
    main()