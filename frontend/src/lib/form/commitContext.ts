export const FORM_FIELD_COMMIT_CONTEXT_KEY = Symbol('form-field-commit');

export type FormFieldCommit = (fieldName: string) => void;
