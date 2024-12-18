from pymongo import MongoClient
from pymongo.errors import ServerSelectionTimeoutError, ConnectionFailure
from bson.objectid import ObjectId
from datetime import datetime, timezone
from app.models import Team, User, Assignment
import logging
import time
import random
import string
from functools import wraps
from typing import Dict

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def with_mongodb_retry(retries=3, delay=2):
    def decorator(f):
        @wraps(f)
        async def wrapper(*args, **kwargs):
            last_error = None
            for attempt in range(retries):
                try:
                    return await f(*args, **kwargs)
                except (ServerSelectionTimeoutError, ConnectionFailure) as e:
                    last_error = e
                    if attempt < retries - 1:
                        logger.warning(f"Attempt {attempt + 1} failed: {str(e)}")
                        time.sleep(delay)
                    else:
                        logger.error(f"All {retries} attempts failed: {str(e)}")
            raise last_error
        return wrapper
    return decorator

class TeamManager:
    def __init__(self, mongo_uri):
        self.mongo_uri = mongo_uri
        self.client = None
        self.db = None
        self.connect()

    def connect(self):
        """Establish connection to MongoDB with basic error handling"""
        try:
            if self.client is None:
                self.client = MongoClient(self.mongo_uri, serverSelectionTimeoutMS=5000)
                self.client.server_info()
                self.db = self.client.get_default_database()
                logger.info("Successfully connected to MongoDB")

                # Ensure teams collection exists
                if "teams" not in self.db.list_collection_names():
                    self.db.create_collection("teams")
                    logger.info("Created teams collection")
        except Exception as e:
            logger.error(f"Failed to connect to MongoDB: {str(e)}")
            raise

    def ensure_connected(self):
        """Ensure we have a valid connection, reconnect if necessary"""
        try:
            if self.client is None:
                self.connect()
            else:
                self.client.server_info()
        except Exception:
            logger.warning("Lost connection to MongoDB, attempting to reconnect...")
            self.connect()

    def generate_join_code(self):
        """Generate a unique 6-character join code"""
        while True:
            code = ''.join(random.choices(string.ascii_uppercase + string.digits, k=6))
            if not self.db.teams.find_one({"team_join_code": code}):
                return code

    @with_mongodb_retry(retries=3, delay=2)
    async def create_team(self, team_number: int, creator_id: str):
        """Create a new team with retry mechanism"""
        self.ensure_connected()
        try:
            # Check for existing team
            if self.db.teams.find_one({"team_number": team_number}):
                return False, "Team already exists"

            team_data = {
                "team_number": team_number,
                "team_join_code": self.generate_join_code(),
                "users": [creator_id],
                "created_at": datetime.now(timezone.utc),
                "created_by": creator_id
            }

            result = self.db.teams.insert_one(team_data)
            
            # Update creator's team number
            self.db.users.update_one(
                {"_id": ObjectId(creator_id)},
                {"$set": {"teamNumber": team_number}}
            )

            logger.info(f"Created new team: {team_number}")
            return True, Team.create_from_db({"_id": result.inserted_id, **team_data})

        except Exception as e:
            logger.error(f"Error creating team: {str(e)}")
            return False, f"Error creating team: {str(e)}"

    @with_mongodb_retry(retries=3, delay=2)
    async def join_team(self, user_id: str, team_join_code: str):
        """Add a user to a team using the join code"""
        self.ensure_connected()
        try:
            team_data = self.db.teams.find_one({"team_join_code": team_join_code})
            if not team_data:
                return False, "Invalid team join code"

            # Check if user is already in the team
            if user_id in team_data.get("users", []):
                return False, "User already in team"

            # Add user to team
            self.db.teams.update_one(
                {"_id": team_data["_id"]}, {"$addToSet": {"users": user_id}}
            )

            # Update user's team number
            self.db.users.update_one(
                {"_id": ObjectId(user_id)},
                {"$set": {"teamNumber": team_data["team_number"]}}
            )

            logger.info(f"User {user_id} joined team {team_data['team_number']}")
            return True, Team.create_from_db(team_data)

        except Exception as e:
            logger.error(f"Error joining team: {str(e)}")
            return False, f"Error joining team: {str(e)}"

    @with_mongodb_retry(retries=3, delay=2)
    async def leave_team(self, user_id: str, team_number: int):
        """Remove a user from a team"""
        self.ensure_connected()
        try:
            # Remove user from team
            result = self.db.teams.update_one(
                {"team_number": team_number}, {"$pull": {"users": user_id}}
            )

            if result.modified_count == 0:
                return False, "User not found in team"

            # Remove team number from user
            self.db.users.update_one(
                {"_id": ObjectId(user_id)},
                {"$unset": {"teamNumber": ""}}
            )

            logger.info(f"User {user_id} left team {team_number}")
            return True, "Successfully left team"

        except Exception as e:
            logger.error(f"Error leaving team: {str(e)}")
            return False, f"Error leaving team: {str(e)}"

    def get_team_by_number(self, team_number: int):
        """Get team by team number"""
        self.ensure_connected()
        try:
            team_data = self.db.teams.find_one({"team_number": team_number})
            return Team.create_from_db(team_data) if team_data else None
        except Exception as e:
            logger.error(f"Error getting team: {str(e)}")
            return None

    def get_team_members(self, team_number: int):
        """Get all members of a team"""
        self.ensure_connected()
        try:
            team = self.db.teams.find_one({"team_number": team_number})
            if not team:
                return []
            
            user_ids = team.get("users", [])
            users = self.db.users.find({"_id": {"$in": [ObjectId(uid) for uid in user_ids]}})
            return [User.create_from_db(user) for user in users]
        except Exception as e:
            logger.error(f"Error getting team members: {str(e)}")
            return []

    @with_mongodb_retry(retries=3, delay=2)
    async def add_admin(self, team_number: int, user_id: str, admin_id: str):
        """Add a new admin to the team"""
        self.ensure_connected()
        try:
            team = self.get_team_by_number(team_number)
            if not team:
                return False, "Team not found"
            
            if not team.is_admin(admin_id):
                return False, "Only team admins can add new admins"

            if user_id not in team.users:
                return False, "User is not a member of this team"

            if user_id in team.admins:
                return False, "User is already an admin"

            self.db.teams.update_one(
                {"team_number": team_number},
                {"$addToSet": {"admins": user_id}}
            )
            return True, "Admin added successfully"
        except Exception as e:
            logger.error(f"Error adding admin: {str(e)}")
            return False, f"Error adding admin: {str(e)}"

    @with_mongodb_retry(retries=3, delay=2)
    async def remove_user(self, team_number: int, user_id: str, admin_id: str):
        """Remove a user from the team"""
        self.ensure_connected()
        try:
            team = self.get_team_by_number(team_number)
            if not team:
                return False, "Team not found"
            
            if not team.is_admin(admin_id):
                return False, "Only team admins can remove users"

            if user_id == team.created_by:
                return False, "Cannot remove the team creator"

            # Remove user from team
            self.db.teams.update_one(
                {"team_number": team_number},
                {
                    "$pull": {
                        "users": user_id,
                        "admins": user_id
                    }
                }
            )

            # Remove team number from user
            self.db.users.update_one(
                {"_id": ObjectId(user_id)},
                {"$unset": {"teamNumber": ""}}
            )

            return True, "User removed successfully"
        except Exception as e:
            logger.error(f"Error removing user: {str(e)}")
            return False, f"Error removing user: {str(e)}"

    @with_mongodb_retry(retries=3, delay=2)
    async def create_assignment(self, team_number: int, creator_id: str, assignment_data: Dict):
        """Create a new assignment for the team"""
        self.ensure_connected()
        try:
            team = self.get_team_by_number(team_number)
            if not team:
                return False, "Team not found"
            
            if not team.is_admin(creator_id):
                return False, "Only team admins can create assignments"

            assignment = {
                "team_number": team_number,
                "title": assignment_data.get("title"),
                "description": assignment_data.get("description", ""),
                "assigned_to": assignment_data.get("assigned_to", []),
                "status": "pending",
                "due_date": assignment_data.get("due_date"),
                "created_by": ObjectId(creator_id),
                "created_at": datetime.now(timezone.utc),
            }

            result = self.db.assignments.insert_one(assignment)
            
            # Add assignment to team
            self.db.teams.update_one(
                {"team_number": team_number},
                {"$addToSet": {"assignments": str(result.inserted_id)}}
            )

            return True, Assignment.create_from_db({"_id": result.inserted_id, **assignment})
        except Exception as e:
            logger.error(f"Error creating assignment: {str(e)}")
            return False, f"Error creating assignment: {str(e)}"

    @with_mongodb_retry(retries=3, delay=2)
    async def update_assignment_status(self, assignment_id: str, user_id: str, new_status: str):
        """Update the status of an assignment"""
        self.ensure_connected()
        try:
            assignment = self.db.assignments.find_one({"_id": ObjectId(assignment_id)})
            if not assignment:
                return False, "Assignment not found"

            if user_id not in assignment.get("assigned_to", []):
                return False, "User is not assigned to this task"

            update_data = {"status": new_status}
            if new_status == "completed":
                update_data["completed_at"] = datetime.now(timezone.utc)

            self.db.assignments.update_one(
                {"_id": ObjectId(assignment_id)},
                {"$set": update_data}
            )

            return True, "Assignment status updated successfully"
        except Exception as e:
            logger.error(f"Error updating assignment status: {str(e)}")
            return False, f"Error updating assignment status: {str(e)}"

    def get_team_assignments(self, team_number: int):
        """Get all assignments for a team"""
        self.ensure_connected()
        try:
            assignments = self.db.assignments.find({"team_number": team_number})
            return [Assignment.create_from_db(assignment) for assignment in assignments]
        except Exception as e:
            logger.error(f"Error getting team assignments: {str(e)}")
            return []

    def __del__(self):
        """Cleanup MongoDB connection"""
        if self.client:
            self.client.close()
