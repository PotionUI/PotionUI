/**
 * Form reaction/conditional engine.
 *
 * Extracted verbatim from DynamicForm.svelte (previously L215-581). This is the ONE
 * reaction evaluator for PotionUI forms - the backend copy (src/api/fields/reactions/)
 * was dead at runtime and has been deleted; reaction validity is now checked only at
 * preset load time via the pydantic ReactionSpec/ConditionSpec/ActionSpec models in
 * src/core/preset/schema.py, which enforce the same operator/action vocabulary below.
 *
 * Spec:
 *
 *   reactions:
 *     - when: <Condition> | <Condition[]> | <LogicalCondition>
 *       then: <Action>
 *
 * `when`:
 *   - A single condition: `{ field: "sampler", equals: "EULER" }` (sugar form) or
 *     `{ field: "sampler", operator: "equals", value: "EULER" }` (explicit form).
 *   - A list of conditions: implicit AND across all of them.
 *   - An explicit logical group: `{ logic: "AND" | "OR", conditions: Condition[] }`.
 *
 * `then` (Action) - any combination of:
 *   - set_visibility: boolean
 *   - set_value: any
 *   - set_disabled: boolean
 *   - update_options: Array<{ label: string; value: string }>
 *   - update_validation: Record<string, any>
 *   - set_filter_tags: string[] | null - a `model`/`lora_picker` field's
 *     `configuration.filter_tags`. Already resolved server-side (any `"@config:<key>"`
 *     indirection is gone by the time this reaches the frontend - see
 *     resolve_reactions_filter_tags in src/features/presets/configuration.py), so this
 *     is applied verbatim, unlike every other action here.
 *
 * Closed operator set (12): equals, not_equals, in, not_in, greater_than, less_than,
 * greater_than_or_equals, less_than_or_equals, contains, not_contains, is_empty,
 * is_not_empty.
 */

import { logger } from '$lib/utils/logger';

// ====================
// Types
// ====================

export interface Condition {
	field: string;
	operator?: string;
	value?: any;
	[key: string]: any; // For direct operator syntax like { field: "sampler", equals: "EULER" }
}

export interface LogicalCondition {
	logic: 'AND' | 'OR';
	conditions: Condition[];
}

export interface Action {
	update_options?: Array<{ label: string; value: string }>;
	set_visibility?: boolean;
	update_validation?: Record<string, any>;
	set_value?: any;
	set_disabled?: boolean;
	set_filter_tags?: string[] | null;
}

export interface Reaction {
	when: Condition | Condition[] | LogicalCondition;
	then: Action;
	description?: string;
	priority?: number;
}

export interface FieldConfig {
	type: string;
	name?: string;
	title?: string;
	configuration?: any;
	reactions?: Reaction[];
	visible?: boolean;
	disabled?: boolean;
	validation?: Record<string, any>;
	value?: any;
	default?: any;
	children?: FieldConfig[];
	/** Simple/Advanced audience toggle (defaults to 'simple' server-side). */
	audience?: 'simple' | 'advanced';
	/** Hidden whenever the Video Director editor owns this preset mode - see
	 *  `$lib/utils/audienceFilter.ts`. */
	hidden_when_video_director?: boolean;
	/** Admin-locked via a per-field form override - folded into `disabled`
	 *  by `$lib/utils/readonlyFilter.ts`, not read directly by field components. */
	readonly?: boolean;
}

// ====================
// Operators
// ====================

export const operators: Record<string, (fieldValue: any, conditionValue?: any) => boolean> = {
	equals: (fieldValue, conditionValue) => fieldValue === conditionValue,
	not_equals: (fieldValue, conditionValue) => fieldValue !== conditionValue,
	in: (fieldValue, conditionValue) => Array.isArray(conditionValue) && conditionValue.includes(fieldValue),
	not_in: (fieldValue, conditionValue) => Array.isArray(conditionValue) && !conditionValue.includes(fieldValue),
	greater_than: (fieldValue, conditionValue) => {
		try {
			return parseFloat(fieldValue) > parseFloat(conditionValue);
		} catch {
			return false;
		}
	},
	less_than: (fieldValue, conditionValue) => {
		try {
			return parseFloat(fieldValue) < parseFloat(conditionValue);
		} catch {
			return false;
		}
	},
	greater_than_or_equals: (fieldValue, conditionValue) => {
		try {
			return parseFloat(fieldValue) >= parseFloat(conditionValue);
		} catch {
			return false;
		}
	},
	less_than_or_equals: (fieldValue, conditionValue) => {
		try {
			return parseFloat(fieldValue) <= parseFloat(conditionValue);
		} catch {
			return false;
		}
	},
	contains: (fieldValue, conditionValue) => {
		try {
			return String(fieldValue).includes(String(conditionValue));
		} catch {
			return false;
		}
	},
	not_contains: (fieldValue, conditionValue) => {
		try {
			return !String(fieldValue).includes(String(conditionValue));
		} catch {
			return true;
		}
	},
	is_empty: (fieldValue) => {
		if (fieldValue === null || fieldValue === undefined) return true;
		if (typeof fieldValue === 'string' || Array.isArray(fieldValue)) return fieldValue.length === 0;
		if (typeof fieldValue === 'object') return Object.keys(fieldValue).length === 0;
		return false;
	},
	is_not_empty: (fieldValue) => {
		if (fieldValue === null || fieldValue === undefined) return false;
		if (typeof fieldValue === 'string' || Array.isArray(fieldValue)) return fieldValue.length > 0;
		if (typeof fieldValue === 'object') return Object.keys(fieldValue).length > 0;
		return true;
	}
};

// ====================
// Condition evaluation
// ====================

/**
 * Evaluates a single field condition
 */
function evaluateFieldCondition(condition: Condition, formData: Record<string, any>): boolean {
	const fieldValue = formData[condition.field];

	// Check for direct operator syntax (e.g., { field: "sampler", equals: "EULER" })
	for (const [key, value] of Object.entries(condition)) {
		if (key !== 'field' && key !== 'operator' && key !== 'value' && operators[key]) {
			return operators[key](fieldValue, value);
		}
	}

	// Check for explicit operator/value syntax
	if (condition.operator && operators[condition.operator]) {
		return operators[condition.operator](fieldValue, condition.value);
	}

	logger.warn(`Unknown operator in condition:`, condition);
	return false;
}

/**
 * Evaluates a logical condition (AND/OR)
 */
function evaluateLogicalCondition(
	condition: LogicalCondition,
	formData: Record<string, any>
): boolean {
	if (!condition.conditions || condition.conditions.length === 0) return true;

	if (condition.logic === 'AND') {
		return condition.conditions.every((c) => evaluateCondition(c, formData));
	} else if (condition.logic === 'OR') {
		return condition.conditions.some((c) => evaluateCondition(c, formData));
	}

	return false;
}

/**
 * Evaluates any type of condition
 */
export function evaluateCondition(
	when: Condition | Condition[] | LogicalCondition,
	formData: Record<string, any>
): boolean {
	if (Array.isArray(when)) {
		// Multiple conditions - implicit AND
		return when.every((condition) => evaluateFieldCondition(condition, formData));
	} else if ('logic' in when && 'conditions' in when) {
		// Explicit logical condition. `Condition`'s index signature keeps TS from
		// narrowing the union on 'in' checks alone; the runtime check above already
		// confirms the LogicalCondition shape.
		return evaluateLogicalCondition(when as LogicalCondition, formData);
	} else {
		// Single field condition
		return evaluateFieldCondition(when, formData);
	}
}

// ====================
// Actions
// ====================

/**
 * Applies an action to a field configuration
 */
export function applyAction(
	fieldConfig: FieldConfig,
	action: Action,
	valueChanges?: Record<string, any>
): FieldConfig {
	const updatedConfig = { ...fieldConfig };

	// `!= null` (not `!== undefined`) throughout: the served schema is a pydantic
	// ActionSpec dump that always emits every action key, so an undeclared action
	// serializes as an explicit `null`. Treating `null` as "present" would reset
	// value/options/validation on every reprocess; `false`/`0`/`''` must still
	// count as "explicitly set", so this is `!= null`, not truthiness.
	if (action.update_options != null) {
		updatedConfig.configuration = {
			...updatedConfig.configuration,
			options: action.update_options
		};
	}

	if (action.set_visibility != null) {
		updatedConfig.visible = action.set_visibility;
	}

	if (action.update_validation != null) {
		updatedConfig.validation = {
			...updatedConfig.validation,
			...action.update_validation
		};
	}

	if (action.set_value != null) {
		updatedConfig.value = action.set_value;
		// Track value changes for syncing to form state
		if (valueChanges && fieldConfig.name) {
			valueChanges[fieldConfig.name] = action.set_value;
		}
	}

	if (action.set_disabled != null) {
		updatedConfig.disabled = action.set_disabled;
	}

	if (action.set_filter_tags != null) {
		updatedConfig.configuration = {
			...updatedConfig.configuration,
			filter_tags: action.set_filter_tags
		};
	}

	return updatedConfig;
}

/**
 * Processes reactions for a single field
 */
export function processFieldReactions(
	fieldConfig: FieldConfig,
	formData: Record<string, any>,
	valueChanges?: Record<string, any>
): FieldConfig {
	if (!fieldConfig.reactions || fieldConfig.reactions.length === 0) {
		return fieldConfig;
	}

	let updatedConfig = { ...fieldConfig };

	for (const reaction of fieldConfig.reactions) {
		try {
			if (evaluateCondition(reaction.when, formData)) {
				updatedConfig = applyAction(updatedConfig, reaction.then, valueChanges);
			}
		} catch (error) {
			logger.warn('Error processing reaction:', reaction, error);
		}
	}

	return updatedConfig;
}

/**
 * Recursively processes reactions for all fields in a form schema
 */
export function processAllFieldReactions(
	fields: FieldConfig[],
	formData: Record<string, any>,
	valueChanges?: Record<string, any>
): FieldConfig[] {
	return fields.map((field) => {
		const updatedField = processFieldReactions(field, formData, valueChanges);

		// Recursively process children if they exist
		if (updatedField.children) {
			updatedField.children = processAllFieldReactions(
				updatedField.children,
				formData,
				valueChanges
			);
		}

		return updatedField;
	});
}

// ====================
// Dependency tracking
// ====================

/**
 * Extracts field dependencies from reactions
 */
export function extractFieldDependencies(fieldConfig: FieldConfig): string[] {
	if (!fieldConfig.reactions) return [];

	const dependencies = new Set<string>();

	const extractFromCondition = (condition: any) => {
		if (Array.isArray(condition)) {
			condition.forEach(extractFromCondition);
		} else if (condition.logic && condition.conditions) {
			condition.conditions.forEach(extractFromCondition);
		} else if (condition.field) {
			dependencies.add(condition.field);
		}
	};

	fieldConfig.reactions.forEach((reaction) => {
		extractFromCondition(reaction.when);
	});

	return Array.from(dependencies);
}

/**
 * Extracts all fields from schema
 */
export function extractAllFields(schema: any): FieldConfig[] {
	if (!schema || !schema.properties) return [];

	const extractFields = (obj: any): FieldConfig[] => {
		const fields: FieldConfig[] = [];

		if (obj.children && Array.isArray(obj.children)) {
			obj.children.forEach((child: any) => {
				fields.push(child);
				if (child.children) {
					fields.push(...extractFields(child));
				}
			});
		}

		return fields;
	};

	const rootProperties = Object.values(schema.properties);
	return rootProperties.flatMap(extractFields);
}

/**
 * Builds dependency map (which fields depend on which other fields)
 */
export function buildDependencyMap(allFields: FieldConfig[]): Record<string, string[]> {
	const map: Record<string, string[]> = {};

	allFields.forEach((field) => {
		if (field.name && field.reactions) {
			const dependencies = extractFieldDependencies(field);
			if (dependencies.length > 0) {
				map[field.name] = dependencies;
			}
		}
	});

	return map;
}

/**
 * Gets fields that should trigger reactions when a specific field changes
 */
export function getFieldsThatTriggerReactions(
	changedFieldName: string,
	dependencyMap: Record<string, string[]>
): string[] {
	const triggeredFields: string[] = [];

	Object.entries(dependencyMap).forEach(([fieldName, dependencies]) => {
		if (dependencies.includes(changedFieldName)) {
			triggeredFields.push(fieldName);
		}
	});

	return triggeredFields;
}

/**
 * Processes schema with reactions and returns updated schema and value changes
 */
export function processSchemaWithReactions(
	schema: any,
	formData: Record<string, any>
): { processedSchema: any; valueChanges: Record<string, any> } {
	if (!schema) return { processedSchema: null, valueChanges: {} };

	// Create a deep copy of the schema
	const updatedSchema = JSON.parse(JSON.stringify(schema));

	// Track value changes from set_value reactions
	const changes: Record<string, any> = {};

	// Process each root property
	Object.keys(updatedSchema.properties).forEach((key) => {
		const rootProperty = updatedSchema.properties[key];
		if (rootProperty.children) {
			rootProperty.children = processAllFieldReactions(rootProperty.children, formData, changes);
		}
	});

	return { processedSchema: updatedSchema, valueChanges: changes };
}
