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
        "mistralai/Mixtral-8x7B-Instruct-v0.1"
    ]

    FALLBACK_TWEETS = [
        "🌍 {trend} matters! Join the discussion. #{trend_clean}",
        "💡 Share your thoughts on {trend}. #{trend_clean}",
        "🤝 Let’s talk {trend}! #{trend_clean}"
    ]

    REQUIRED_VARS = [
        "TWITTER_API_KEY", "TWITTER_API_SECRET",
        "TWITTER_ACCESS_TOKEN", "TWITTER_ACCESS_SECRET",
        "HUGGINGFACE_API_TOKEN"
    ]

    CONTENT_SAFETY = {
        "banned_phrases": [
            "blown away", "breaking news", "exposed", "leaked",
            "factoryBuilder", "ViralForChange",
            "Join me", "Check out", "Purchase", "Click here",
            "The tweet starts with", "Generate a tweet",
            "Write a tweet", "Example", "Test", 
            "Question", "To craft", "Did you know",
            "Remember to", "Avoid", "Keep it", "Use", 
            "Write", "Tip", "Fact", "Resource",
            "in order to", "please note", "ensure that",
            "focus on", "emphasize", "highlight", "strive for",
            "create a", "craft a", "compose a", "put together",
            "Dont miss", "subscribe", "Watch out",
            "Your post should", "Heres an", "alternative version",
            "similarly", "likewise", "conversely", "however",
            "moreover", "furthermore", "nevertheless",
            "in addition", "additionally", "further"
        ],
        "allowed_emojis": ["📊", "🔍", "💡", "🌍", "💬", "🌟", "🚀"],
        "call_to_action": ["Let’s", "Join", "Share", "Act now", "Discuss"]
    }

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

    def post_tweet(self, text: str) -> None:
        for attempt in range(BotConfig.MAX_RETRIES):
            try:
                response = self.client.create_tweet(text=text)
                logging.info(f"Tweet posted: {response.data['id']}")
                return
            except tweepy.TooManyRequests as e:
                reset_time = int(e.response.headers.get('x-rate-limit-reset', time.time() + 300))
                wait = max(reset_time - time.time(), 0)
                logging.warning(f"Rate limit hit; waiting {wait:.1f}s...")
                time.sleep(wait)
            except tweepy.Forbidden as e:
                logging.error(f"Content policy violation: {e.api_errors}")
                return
            except Exception as e:
                logging.error(f"Tweet failed (attempt {attempt+1}): {str(e)[:100]}")
                time.sleep(2 ** attempt)

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
                "temperature": 0.8,
                "top_p": 0.9,
                "repetition_penalty": 1.5,
                "return_full_text": False
            }
        }

        for model in BotConfig.MODEL_ENDPOINTS:
            try:
                response = requests.post(
                    f"https://api-inference.huggingface.co/models/{model}",
                    headers=headers,
                    json=data,
                    timeout=30
                )
                response.raise_for_status()
                return self._extract_generated_text(response.json(), prompt)
            except requests.exceptions.RequestException as e:
                logging.error(f"Request error for {model}: {str(e)[:100]}")
            except Exception as e:
                logging.error(f"Unexpected error with {model}: {str(e)[:100]}")
        return None

    def _extract_generated_text(self, response, prompt: str) -> Optional[str]:
        try:
            generated = response[0]["generated_text"].strip()
            # Remove the prompt from the generated text if it appears
            if generated.startswith(prompt):
                generated = generated[len(prompt):].strip()
            return generated
        except (KeyError, IndexError):
            logging.error(f"Invalid model response: {response}")
            return None

class TrendAnalyzer:
    @staticmethod
    def get_trends() -> List[str]:
        try:
            pytrends = TrendReq(hl='en-US', tz=360, geo='US')
            trends_df = pytrends.trending_searches()
            if trends_df.empty:
                return ["AI", "Climate", "Healthcare"]
            trends = trends_df['title'].tolist() if 'title' in trends_df.columns else trends_df.iloc[:, 0].tolist()
            return [
                t.strip() for t in trends 
                if len(t.strip()) > 4 and not t.strip().isdigit()
            ][:5] or ["AI", "Climate", "Healthcare"]
        except Exception as e:
            logging.error(f"Trend retrieval failed: {e}")
            return ["AI", "Climate", "Healthcare"]

class ContentFormatter:
    @staticmethod
    def format_tweet(raw_text: str, trend: str) -> str:
        # Use the generated text directly as tweet content
        tweet = raw_text.strip()

        # If the tweet doesn't mention the trend, append a hashtag for it
        if trend.lower() not in tweet.lower():
            trend_clean = re.sub(r'\W+', '', trend).strip()
            hashtag = f" #{trend} #{trend_clean}{datetime.now().year}"
            tweet += hashtag

        # Ensure tweet length does not exceed Twitter's limit
        if len(tweet) > 280:
            tweet = tweet[:280].rsplit(' ', 1)[0] + "..."
        return tweet if len(tweet) > 10 else "No viable content generated"

def main():
    try:
        client = TwitterClient()
        generator = ContentGenerator()
        trends = TrendAnalyzer.get_trends()
        selected_trend = random.choice(trends)
        logging.info(f"Selected trend: {selected_trend}")

        prompt = (
            f"Write a human-like tweet about {selected_trend} that sparks curiosity and engagement. "
            "Ensure the tweet is under 280 characters. Only output the tweet text, without any instructions or additional text. 🌟"
        )

        raw_content = generator.generate(prompt)
        if not raw_content:
            logging.warning("Content generation failed. Using fallback.")
            trend_clean = re.sub(r'\W+', '', selected_trend)
            fallback = random.choice(BotConfig.FALLBACK_TWEETS).format(
                trend=selected_trend,
                trend_clean=trend_clean
            )
            client.post_tweet(fallback)
        else:
            tweet_text = ContentFormatter.format_tweet(raw_content, selected_trend)
            logging.info(f"Final tweet: {tweet_text}")
            client.post_tweet(tweet_text)
        
    except KeyboardInterrupt:
        logging.info("Bot interrupted by user. Exiting...")
        sys.exit(0)
    except Exception as e:
        logging.critical(f"Critical failure: {str(e)}")
        sys.exit(1)

if __name__ == "__main__":
    main()
