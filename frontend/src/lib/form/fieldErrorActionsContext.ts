import type { FieldErrorFix } from '$lib/utils/formValidationErrors';

/**
 * Optional actions exposed by DynamicForm to leaf fields that offer a server
 * validation quick-fix affecting more than the field currently being edited.
 */
export interface FormFieldErrorActions {
	clearFields(names: string[]): void;
	setFieldValue(name: string, value: unknown): void;
}

export const FORM_FIELD_ERROR_ACTIONS_CONTEXT_KEY = 'formFieldErrorActions';

/**
 * Writes a field's quick-fix value and drops the error messages that value
 * resolves — including the ones sitting on the *other* fields the same backend
 * message was attached to, which no edit of this field would otherwise clear.
 */
export function applyFieldErrorFix(fix: FieldErrorFix, actions: FormFieldErrorActions): void {
	actions.setFieldValue(fix.fieldName, fix.value);
	actions.clearFields(fix.resolvesFields);
}
