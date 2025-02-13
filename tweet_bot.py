#!/usr/bin/env python3
import os
import sys
import random
import time
import re  # <--- Add this line
import requests
import tweepy
import logging
from datetime import datetime
from typing import List, Optional
from dotenv import load_dotenv
from pytrends.request import TrendReq

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[logging.FileHandler('bot.log'), logging.StreamHandler()]
)

load_dotenv()

class BotConfig:
    MODEL_ENDPOINTS = [
        "HuggingFaceH4/zephyr-7b-beta",
        "mistralai/Mixtral-8x7B-Instruct-v0.1",
        "google/gemma-7b-it"
    ]

    FALLBACK_TWEETS = [
        "🚀 {trend} is making waves! Stay tuned for more updates. #{trend_clean}",
        "📈 {trend} is trending! What's your take? #{trend_clean}",
        "🔍 Exploring {trend}... stay curious! #{trend_clean}"
    ]

    REQUIRED_VARS = [
        "TWITTER_API_KEY", "TWITTER_API_SECRET",
        "TWITTER_ACCESS_TOKEN", "TWITTER_ACCESS_SECRET",
        "HUGGINGFACE_API_TOKEN"
    ]

    CONTENT_SAFETY = {
        "banned_phrases": [
            "blown away","breaking news", "exposed", "leaked", "🚨", "😱", "💣"
        ],
        "allowed_emojis": ["📈", "📊", "🔍", "🚀", "💡", "👀", "🌍", "🛒", "🍔"]
    }

    RATE_LIMIT_BACKOFF = 300  # 5 minutes
    MAX_RETRIES = 3

class TwitterClient:
    def __init__(self):
        self._validate_twitter_credentials()
        self.client = self._init_twitter_client()

    def _validate_twitter_credentials(self):
        for var in BotConfig.REQUIRED_VARS[:-1]:
            if not os.getenv(var):
                logging.critical(f"Missing Twitter credential: {var}")
                raise SystemExit(1)

    def _init_twitter_client(self):
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
                logging.info(f"Tweet posted successfully: {response.data['id']}")
                return
            except tweepy.TooManyRequests as e:
                self._handle_rate_limit(e, attempt)
            except tweepy.Forbidden as e:
                logging.critical(f"Content policy violation: {e.api_errors}")
                return
            except Exception as e:
                logging.error(f"Tweet posting failed: {str(e)[:100]}")
                return
            finally:
                attempt += 1

    def _handle_rate_limit(self, error, attempt):
        reset_time = int(error.response.headers.get('x-rate-limit-reset', time.time() + 300))
        wait_time = max(reset_time - int(time.time()), 0)
        
        if wait_time > BotConfig.RATE_LIMIT_BACKOFF:
            logging.error(f"Rate limit exceeded. Wait time {wait_time}s exceeds threshold. Aborting.")
            return
        
        logging.warning(f"Rate limit hit (attempt {attempt+1}/{BotConfig.MAX_RETRIES}). "
                        f"Waiting {wait_time}s before retry...")
        time.sleep(wait_time)

class ContentGenerator:
    def __init__(self):
        self._validate_hf_token()

    def _validate_hf_token(self):
        if not os.getenv("HUGGINGFACE_API_TOKEN"):
            logging.critical("Missing HuggingFace API token")
            raise SystemExit(1)

    def generate(self, prompt: str) -> Optional[str]:
        headers = {
            "Authorization": f"Bearer {os.getenv('HUGGINGFACE_API_TOKEN')}",
            "User-Agent": "TwitterBot/1.0"
        }
        data = {
            "inputs": prompt,
            "parameters": {
                "max_new_tokens": 80,
                "temperature": 0.6,
                "top_p": 0.85,
                "repetition_penalty": 1.3
            }
        }

        for model in random.sample(BotConfig.MODEL_ENDPOINTS, len(BotConfig.MODEL_ENDPOINTS)):
            try:
                response = requests.post(
                    f"https://api-inference.huggingface.co/models/{model}",
                    headers=headers,
                    json=data,
                    timeout=15
                )
                response.raise_for_status()
                result = response.json()
                return result[0].get("generated_text", "").split("Example:")[0].strip()
            except Exception as e:
                logging.error(f"Model {model[:15]}... failed: {str(e)[:100]}")
        
        return None

class TrendAnalyzer:
    @staticmethod
    def get_trends(country: str = 'india') -> List[str]:
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
        
        # Remove banned content
        for phrase in BotConfig.CONTENT_SAFETY["banned_phrases"]:
            clean_text = clean_text.replace(phrase, "")
        
        # Year normalization
        clean_text = re.sub(r'\b(20\d{2})\b', str(current_year), clean_text)
        
        # Hashtag management
        trend_clean = trend.replace(" ", "")
        hashtags = [f"#{trend_clean}", f"#{trend_clean}{current_year}"]
        clean_text += " " + " ".join(hashtags)
        
        # Emoji safety
        clean_text = ''.join(c for c in clean_text if c in BotConfig.CONTENT_SAFETY["allowed_emojis"] or c.isalnum() or c in ' #')
        
        # Length enforcement
        if len(clean_text) > 275:
            clean_text = clean_text[:250].rsplit(' ', 1)[0] + "…"
        
        return clean_text.strip()

def main():
    try:
        # Initialization
        client = TwitterClient()
        generator = ContentGenerator()
        trends = TrendAnalyzer.get_trends()
        
        # Trend selection
        selected_trend = random.choice(trends)
        logging.info(f"Selected trend: {selected_trend}")
        
        # Content generation
        prompt = (
            f"Write a concise tweet about {selected_trend}. "
            "Include: a bold question/statement, 2-3 trending hashtags, "
            "and a call to action like 'RT if you agree!'. "
            "Keep under 250 characters. Avoid sensational language."
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
        
        # Final formatting
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