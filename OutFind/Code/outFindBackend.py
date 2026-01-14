from fastapi import FastAPI, HTTPException, Depends, File, UploadFile
from fastapi.middleware.cors import CORSMiddleware
import firebase_admin
from firebase_admin import credentials, auth
from typing import List, Optional
import uvicorn
from pydantic import BaseModel
from datetime import datetime
import asyncpg
import os
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Initialize Firebase Admin
cred = credentials.Certificate("path/to/firebase-credentials.json")
firebase_admin.initialize_app(cred)

app = FastAPI()

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Database connection
async def get_db_pool():
    return await asyncpg.create_pool(os.getenv("DATABASE_URL"))

# Models
class User(BaseModel):
    id: str
    username: str
    bio: Optional[str] = None
    profile_image_url: Optional[str] = None
    followers_count: int = 0
    following_count: int = 0
    style_preferences: List[str] = []

class Post(BaseModel):
    id: str
    user_id: str
    image_url: str
    caption: Optional[str] = None
    likes: int = 0
    created_at: datetime
    tags: List[str] = []

class ClothingItem(BaseModel):
    id: str
    user_id: str
    image_url: str
    category: str
    style: List[str]
    created_at: datetime

# Authentication middleware
async def verify_token(authorization: str = Depends()):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Invalid authorization header")
    
    token = authorization.split(" ")[1]
    try:
        decoded_token = auth.verify_id_token(token)
        return decoded_token
    except Exception as e:
        raise HTTPException(status_code=401, detail="Invalid token")

# Routes

# Authentication
@app.post("/api/auth/verify")
async def verify_auth(token_data=Depends(verify_token)):
    return {"user_id": token_data["uid"]}

# User Routes
@app.get("/api/users/{user_id}")
async def get_user(user_id: str, pool=Depends(get_db_pool)):
    async with pool.acquire() as conn:
        user = await conn.fetchrow(
            """
            SELECT * FROM users WHERE id = $1
            """,
            user_id
        )
        if not user:
            raise HTTPException(status_code=404, detail="User not found")
        return dict(user)

@app.post("/api/users/{user_id}/style-preferences")
async def update_style_preferences(
    user_id: str,
    preferences: List[str],
    pool=Depends(get_db_pool),
    token_data=Depends(verify_token)
):
    if token_data["uid"] != user_id:
        raise HTTPException(status_code=403, detail="Unauthorized")
    
    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE users SET style_preferences = $1 WHERE id = $2
            """,
            preferences, user_id
        )
    return {"status": "success"}

# Post Routes
@app.post("/api/posts")
async def create_post(
    image: UploadFile = File(...),
    caption: Optional[str] = None,
    tags: List[str] = [],
    pool=Depends(get_db_pool),
    token_data=Depends(verify_token)
):
    # Handle image upload (implement your storage solution)
    image_url = await upload_image(image)
    
    async with pool.acquire() as conn:
        post_id = await conn.fetchval(
            """
            INSERT INTO posts (user_id, image_url, caption, tags)
            VALUES ($1, $2, $3, $4)
            RETURNING id
            """,
            token_data["uid"], image_url, caption, tags
        )
    return {"post_id": post_id}

@app.get("/api/feed")
async def get_feed(
    page: int = 1,
    pool=Depends(get_db_pool),
    token_data=Depends(verify_token)
):
    async with pool.acquire() as conn:
        # Get user's style preferences
        user = await conn.fetchrow(
            "SELECT style_preferences FROM users WHERE id = $1",
            token_data["uid"]
        )
        
        # Fetch personalized feed
        posts = await conn.fetch(
            """
            SELECT p.*, u.username, u.profile_image_url as user_profile_image
            FROM posts p
            JOIN users u ON p.user_id = u.id
            WHERE $1 && p.tags
            OR p.user_id IN (
                SELECT following_id FROM followers WHERE follower_id = $2
            )
            ORDER BY p.created_at DESC
            LIMIT 20 OFFSET $3
            """,
            user["style_preferences"], token_data["uid"], (page - 1) * 20
        )
        return [dict(post) for post in posts]

# Closet Routes
@app.post("/api/closet/items")
async def add_clothing_item(
    image: UploadFile = File(...),
    category: str,
    style: List[str],
    pool=Depends(get_db_pool),
    token_data=Depends(verify_token)
):
    # Handle image upload and background removal
    image_url = await upload_and_process_image(image)
    
    async with pool.acquire() as conn:
        item_id = await conn.fetchval(
            """
            INSERT INTO clothing_items (user_id, image_url, category, style)
            VALUES ($1, $2, $3, $4)
            RETURNING id
            """,
            token_data["uid"], image_url, category, style
        )
    return {"item_id": item_id}

@app.post("/api/outfits/generate")
async def generate_outfit(
    style: str,
    categories: List[str],
    pool=Depends(get_db_pool),
    token_data=Depends(verify_token)
):
    async with pool.acquire() as conn:
        # Get random items from each category
        items = []
        for category in categories:
            item = await conn.fetchrow(
                """
                SELECT * FROM clothing_items
                WHERE user_id = $1 AND category = $2 AND $3 = ANY(style)
                ORDER BY RANDOM()
                LIMIT 1
                """,
                token_data["uid"], category, style
            )
            if item:
                items.append(dict(item))
    
    return {
        "outfit_id": str(datetime.now().timestamp()),
        "items": items,
        "style": style
    }

# Following Routes
@app.post("/api/users/{user_id}/follow")
async def follow_user(
    user_id: str,
    pool=Depends(get_db_pool),
    token_data=Depends(verify_token)
):
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO followers (follower_id, following_id)
            VALUES ($1, $2)
            ON CONFLICT DO NOTHING
            """,
            token_data["uid"], user_id
        )
        
        # Update counts
        await conn.execute(
            """
            UPDATE users SET followers_count = followers_count + 1
            WHERE id = $1
            """,
            user_id
        )
        await conn.execute(
            """
            UPDATE users SET following_count = following_count + 1
            WHERE id = $1
            """,
            token_data["uid"]
        )
    
    return {"status": "success"}

# Database initialization
async def init_db():
    pool = await asyncpg.create_pool(os.getenv("DATABASE_URL"))
    async with pool.acquire() as conn:
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id TEXT PRIMARY KEY,
                username TEXT UNIQUE NOT NULL,
                bio TEXT,
                profile_image_url TEXT,
                followers_count INTEGER DEFAULT 0,
                following_count INTEGER DEFAULT 0,
                style_preferences TEXT[],
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS posts (
                id SERIAL PRIMARY KEY,
                user_id TEXT REFERENCES users(id),
                image_url TEXT NOT NULL,
                caption TEXT,
                likes INTEGER DEFAULT 0,
                tags TEXT[],
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS clothing_items (
                id SERIAL PRIMARY KEY,
                user_id TEXT REFERENCES users(id),
                image_url TEXT NOT NULL,
                category TEXT NOT NULL,
                style TEXT[],
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS followers (
                follower_id TEXT REFERENCES users(id),
                following_id TEXT REFERENCES users(id),
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (follower_id, following_id)
            )
        """)

if __name__ == "__main__":
    import asyncio
    asyncio.run(init_db())
    uvicorn.run(app, host="0.0.0.0", port=8000)