import os
import random
import requests
import tweepy
from tenacity import retry, stop_after_attempt, wait_exponential

# Load environment variables
api_key = os.getenv("TWITTER_API_KEY")
api_secret = os.getenv("TWITTER_API_SECRET")
access_token = os.getenv("TWITTER_ACCESS_TOKEN")
access_secret = os.getenv("TWITTER_ACCESS_SECRET")
hf_api_token = os.getenv("HUGGINGFACE_API_TOKEN")

# Authenticate with Twitter API v2
client = tweepy.Client(
    consumer_key=api_key,
    consumer_secret=api_secret,
    access_token=access_token,
    access_token_secret=access_secret
)

# Fetch trending topics (API v1.1)
auth = tweepy.OAuth1UserHandler(api_key, api_secret, access_token, access_secret)
api = tweepy.API(auth)

def get_trending_topics():
    try:
        trends = api.get_place_trends(id=1)  # WOEID 1 = Worldwide
        return [t['name'] for t in trends[0]['trends'] if not t['promoted_content']]
    except Exception as e:
        print(f"Error fetching trends: {e}")
        return []

@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, max=10))
def generate_tweet(prompt):
    headers = {"Authorization": f"Bearer {hf_api_token}"}
    data = {"inputs": prompt}
    try:
        response = requests.post(
            "https://api-inference.huggingface.co/models/mistralai/Mistral-7B-instruct-v0.2",
            headers=headers,
            json=data,
            timeout=20
        )
        response.raise_for_status()
        return response.json()[0]['generated_text']
    except Exception as e:
        print(f"Error generating tweet: {e}")
        return "Error generating tweet."

def post_tweet(text):
    try:
        if len(text) > 280:
            text = text[:277] + "..."
        client.create_tweet(text=text)
        print("Tweet posted!")
    except Exception as e:
        print(f"Error posting tweet: {e}")

if __name__ == "__main__":
    trends = get_trending_topics()
    prompt = f"""
    Write a viral tweet about {random.choice(trends or ['AI trends'])}.
    Include:
    - A bold statement or question.
    - Trending hashtags (e.g., #AI, #{random.choice(trends or ['Tech'])}).
    - A call to action (e.g., "RT if you agree!").
    Keep under 280 characters.
    """
    tweet = generate_tweet(prompt)
    post_tweet(tweet)