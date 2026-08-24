"""
Create segment tables (categories and templates)
"""

from src.platform.database.database import db

def up():
    """Create segment tables"""
    with db.get_cursor() as cursor:
        # Create segment categories table
        cursor.execute("""
            CREATE TABLE segment_categories (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL UNIQUE,
                description TEXT NOT NULL DEFAULT '',
                color TEXT NOT NULL DEFAULT '#3B82F6',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # Create segment templates table
        cursor.execute("""
            CREATE TABLE segment_templates (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                content TEXT NOT NULL,
                category_id TEXT NOT NULL,
                description TEXT NOT NULL DEFAULT '',
                tags TEXT NOT NULL DEFAULT '[]',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (category_id) REFERENCES segment_categories(id) ON DELETE CASCADE,
                UNIQUE(name, category_id)
            )
        """)
        
        # Create triggers to update updated_at
        cursor.execute("""
            CREATE TRIGGER update_segment_categories_updated_at 
            AFTER UPDATE ON segment_categories 
            FOR EACH ROW 
            BEGIN 
                UPDATE segment_categories SET updated_at = CURRENT_TIMESTAMP WHERE id = NEW.id;
            END
        """)
        
        cursor.execute("""
            CREATE TRIGGER update_segment_templates_updated_at 
            AFTER UPDATE ON segment_templates 
            FOR EACH ROW 
            BEGIN 
                UPDATE segment_templates SET updated_at = CURRENT_TIMESTAMP WHERE id = NEW.id;
            END
        """)
        
        # Create indices for performance
        cursor.execute("CREATE INDEX idx_segment_templates_category_id ON segment_templates(category_id)")
        cursor.execute("CREATE INDEX idx_segment_templates_name ON segment_templates(name)")
        
        # Insert default categories
        cursor.execute("""
            INSERT INTO segment_categories (id, name, description, color) VALUES 
            ('quality', 'Quality & Technical', 'Quality enhancing prompts and technical specifications', '#10B981'),
            ('style', 'Art Style', 'Artistic styles and aesthetic directions', '#8B5CF6'),
            ('environment', 'Environment', 'Lighting, atmosphere, and environmental settings', '#F59E0B'),
            ('composition', 'Composition', 'Camera angles, framing, and composition techniques', '#EF4444')
        """)
        
        # Insert default templates
        cursor.execute("""
            INSERT INTO segment_templates (id, name, content, category_id, description, tags) VALUES 
            ('high_quality', 'High Quality', 'masterpiece, best quality, ultra detailed, 8k, high resolution, professional', 'quality', 'Standard high-quality enhancement tags', '["quality", "resolution", "professional"]'),
            ('cinematic_lighting', 'Cinematic Lighting', 'cinematic lighting, dramatic shadows, golden hour, rim lighting, volumetric lighting', 'environment', 'Professional cinematic lighting setup', '["lighting", "cinematic", "atmospheric"]'),
            ('portrait_composition', 'Portrait Composition', 'portrait, upper body, medium shot, shallow depth of field, bokeh background', 'composition', 'Standard portrait composition setup', '["portrait", "composition", "dof"]')
        """)

def down():
    """Drop segment tables"""
    with db.get_cursor() as cursor:
        # Drop triggers
        cursor.execute("DROP TRIGGER IF EXISTS update_segment_categories_updated_at")
        cursor.execute("DROP TRIGGER IF EXISTS update_segment_templates_updated_at")
        
        # Drop indices
        cursor.execute("DROP INDEX IF EXISTS idx_segment_templates_category_id")
        cursor.execute("DROP INDEX IF EXISTS idx_segment_templates_name")
        
        # Drop tables (templates first due to foreign key)
        cursor.execute("DROP TABLE IF EXISTS segment_templates")
        cursor.execute("DROP TABLE IF EXISTS segment_categories")