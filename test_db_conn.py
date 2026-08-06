import asyncio
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import declarative_base, sessionmaker
from sqlalchemy import Column, Integer, String, select

# 1. Setup Connection URL using your updated username
DATABASE_URL = "postgresql+asyncpg://postgres:walia@localhost:5432/health_db"

# 2. Initialize the Async Engine and Session
engine = create_async_engine(DATABASE_URL, echo=True)
AsyncSessionLocal = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
Base = declarative_base()

# 3. Define a Sample Table Model (Users)
class User(Base):
    __tablename__ = "users"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String, nullable=False)
    email = Column(String, unique=True, nullable=False)

async def main():
    # 4. Create the table in health_db
    async with engine.begin() as conn:
        print("Creating tables...")
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
        print("Tables created successfully!")

    # 5. Insert and Query Data
    async with AsyncSessionLocal() as session:
        async with session.begin():
            # Insert Sample Rows
            user1 = User(name="Alice Smith", email="alice@example.com")
            user2 = User(name="Bob Jones", email="bob@example.com")
            session.add_all([user1, user2])
            print("Sample data inserted!")

        # Query the database
        print("\n--- Querying Results ---")
        result = await session.execute(select(User))
        users = result.scalars().all()
        
        for user in users:
            print(f"ID: {user.id} | Name: {user.name} | Email: {user.email}")

    # Clean up connections
    await engine.dispose()

if __name__ == "__main__":
    asyncio.run(main())
