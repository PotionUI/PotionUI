"""
Migration 008: Add user_id columns for user data isolation
Adds user_id column to generations, llm_commands, llm_prompt_styles, segment_categories, and segment_templates tables
"""

def up():
    """Add user_id columns to all user-scoped tables"""
    from src.platform.database.database import db
    
    with db.get_cursor() as cursor:
        # Add user_id to generations table
        cursor.execute("""
            ALTER TABLE generations 
            ADD COLUMN user_id TEXT REFERENCES users(id) ON DELETE CASCADE
        """)
        
        # Add user_id to generation_files table
        cursor.execute("""
            ALTER TABLE generation_files 
            ADD COLUMN user_id TEXT REFERENCES users(id) ON DELETE CASCADE
        """)
        
        # Add user_id to llm_commands table
        cursor.execute("""
            ALTER TABLE llm_commands 
            ADD COLUMN user_id TEXT REFERENCES users(id) ON DELETE CASCADE
        """)
        
        # Add user_id to llm_prompt_styles table
        cursor.execute("""
            ALTER TABLE llm_prompt_styles 
            ADD COLUMN user_id TEXT REFERENCES users(id) ON DELETE CASCADE
        """)
        
        # Add user_id to segment_categories table
        cursor.execute("""
            ALTER TABLE segment_categories 
            ADD COLUMN user_id TEXT REFERENCES users(id) ON DELETE CASCADE
        """)
        
        # Add user_id to segment_templates table
        cursor.execute("""
            ALTER TABLE segment_templates 
            ADD COLUMN user_id TEXT REFERENCES users(id) ON DELETE CASCADE
        """)
        
        # For existing data, we need to assign to the first admin user if any exists
        # Get the first admin user
        cursor.execute("SELECT id FROM users WHERE account_type = 'ADMIN' ORDER BY created_at ASC LIMIT 1")
        admin_user = cursor.fetchone()
        
        if admin_user:
            admin_id = admin_user['id']
            
            # Update existing generations
            cursor.execute("UPDATE generations SET user_id = ? WHERE user_id IS NULL", (admin_id,))
            
            # Update existing generation_files
            cursor.execute("UPDATE generation_files SET user_id = ? WHERE user_id IS NULL", (admin_id,))
            
            # Update existing llm_commands
            cursor.execute("UPDATE llm_commands SET user_id = ? WHERE user_id IS NULL", (admin_id,))
            
            # Update existing llm_prompt_styles
            cursor.execute("UPDATE llm_prompt_styles SET user_id = ? WHERE user_id IS NULL", (admin_id,))
            
            # Update existing segment_categories
            cursor.execute("UPDATE segment_categories SET user_id = ? WHERE user_id IS NULL", (admin_id,))
            
            # Update existing segment_templates
            cursor.execute("UPDATE segment_templates SET user_id = ? WHERE user_id IS NULL", (admin_id,))
            
            print(f"Assigned existing data to admin user: {admin_id}")
        else:
            print("No admin user found, existing data will remain unassigned")

        # Only update existing data if there is any
        # In test environments with no data, skip these updates
        cursor.execute("SELECT COUNT(*) FROM generations WHERE user_id IS NULL")
        if cursor.fetchone()[0] > 0:
            # Create an 'unknown' user for orphaned data
            cursor.execute("""
                INSERT OR IGNORE INTO users (id, username, account_type)
                VALUES ('unknown', 'unknown', 'USER')
            """)

            # Make user_id NOT NULL after assigning existing data
            cursor.execute("UPDATE generations SET user_id = 'unknown' WHERE user_id IS NULL")
            cursor.execute("UPDATE generation_files SET user_id = 'unknown' WHERE user_id IS NULL")
            cursor.execute("UPDATE llm_commands SET user_id = 'unknown' WHERE user_id IS NULL")
            cursor.execute("UPDATE llm_prompt_styles SET user_id = 'unknown' WHERE user_id IS NULL")
            cursor.execute("UPDATE segment_categories SET user_id = 'unknown' WHERE user_id IS NULL")
            cursor.execute("UPDATE segment_templates SET user_id = 'unknown' WHERE user_id IS NULL")
        
        print("Migration 008: Added user_id columns for user data isolation")


def down():
    """Remove user_id columns from all tables"""
    from src.platform.database.database import db
    
    with db.get_cursor() as cursor:
        cursor.execute("ALTER TABLE generations DROP COLUMN user_id")
        cursor.execute("ALTER TABLE generation_files DROP COLUMN user_id")
        cursor.execute("ALTER TABLE llm_commands DROP COLUMN user_id")
        cursor.execute("ALTER TABLE llm_prompt_styles DROP COLUMN user_id")
        cursor.execute("ALTER TABLE segment_categories DROP COLUMN user_id")
        cursor.execute("ALTER TABLE segment_templates DROP COLUMN user_id")
        
        print("Migration 008: Removed user_id columns")