from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    discord_token: str
    owner_id: int
    google_token: str
    
    class Config:
        env_file = ".env"

settings = Settings()