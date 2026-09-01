from typing import List, Optional, Dict, Any
import json
from src.features.generation.records import GenerationParameter
from src.platform.util.ids import generate_ulid

class GenerationParameterRepository:
    def create(self, parameter: GenerationParameter) -> GenerationParameter:
        """Create a new generation parameter"""
        if not parameter.id:
            parameter.id = generate_ulid()

        from src.platform.database.database import db
        with db.get_cursor() as cursor:
            cursor.execute("""
                INSERT INTO generation_parameters (
                    id, generation_id, parameter_name, 
                    parameter_value, parameter_index
                ) VALUES (?, ?, ?, ?, ?)
            """, (
                parameter.id,
                parameter.generation_id,
                parameter.parameter_name,
                parameter.parameter_value,
                parameter.parameter_index
            ))
            
            cursor.execute("SELECT * FROM generation_parameters WHERE id = ?", (parameter.id,))
            row = cursor.fetchone()
            
            if not row:
                return None
            
            return GenerationParameter.from_row(row)
    
    def create_batch(self, generation_id: str, param_name: str, values: List[Any]) -> List[GenerationParameter]:
        """Create multiple parameters for a generation"""
        parameters = []

        from src.platform.database.database import db
        with db.get_cursor() as cursor:
            for index, value in enumerate(values):
                # Check if parameter already exists
                cursor.execute("""
                    SELECT * FROM generation_parameters 
                    WHERE generation_id = ? AND parameter_name = ? AND parameter_index = ?
                """, (generation_id, param_name, index))
                
                existing_row = cursor.fetchone()
                if existing_row:
                    # Parameter already exists, use it
                    parameters.append(GenerationParameter.from_row(existing_row))
                    continue
                
                # Create new parameter
                param_id = generate_ulid()
                json_value = json.dumps(value)
                
                cursor.execute("""
                    INSERT INTO generation_parameters (
                        id, generation_id, parameter_name, 
                        parameter_value, parameter_index
                    ) VALUES (?, ?, ?, ?, ?)
                """, (
                    param_id,
                    generation_id,
                    param_name,
                    json_value,
                    index
                ))
                
                cursor.execute("SELECT * FROM generation_parameters WHERE id = ?", (param_id,))
                row = cursor.fetchone()
                if row:
                    parameters.append(GenerationParameter.from_row(row))
        
        return parameters
    
    def get_by_generation(self, generation_id: str) -> List[GenerationParameter]:
        """Get all parameters for a generation"""
        from src.platform.database.database import db
        with db.get_cursor() as cursor:
            cursor.execute("""
                SELECT * FROM generation_parameters 
                WHERE generation_id = ?
                ORDER BY parameter_name, parameter_index
            """, (generation_id,))
            
            return [GenerationParameter.from_row(row) for row in cursor.fetchall()]
    
    def get_by_generation_and_index(self, generation_id: str, parameter_index: int) -> List[GenerationParameter]:
        """Get all parameters for a specific generation and index"""
        from src.platform.database.database import db
        with db.get_cursor() as cursor:
            cursor.execute("""
                SELECT * FROM generation_parameters 
                WHERE generation_id = ? AND parameter_index = ?
                ORDER BY parameter_name
            """, (generation_id, parameter_index))
            
            return [GenerationParameter.from_row(row) for row in cursor.fetchall()]
    
    def get_by_name(self, generation_id: str, parameter_name: str) -> List[GenerationParameter]:
        """Get all values for a specific parameter name"""
        from src.platform.database.database import db
        with db.get_cursor() as cursor:
            cursor.execute("""
                SELECT * FROM generation_parameters 
                WHERE generation_id = ? AND parameter_name = ?
                ORDER BY parameter_index
            """, (generation_id, parameter_name))
            
            return [GenerationParameter.from_row(row) for row in cursor.fetchall()]
    
    def delete_by_generation(self, generation_id: str) -> int:
        """Delete all parameters for a generation"""
        from src.platform.database.database import db
        with db.get_cursor() as cursor:
            cursor.execute(
                "DELETE FROM generation_parameters WHERE generation_id = ?",
                (generation_id,)
            )
            return cursor.rowcount

# Global repository instance
generation_parameter_repo = GenerationParameterRepository()