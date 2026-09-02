// Pure helpers for PromptImportModal - kept free of Svelte/DOM so they can be
// unit tested directly (FormData is available in both browser and vitest/jsdom).

export type PromptImportFormatValue = '' | 'styles_csv' | 'style_json' | 'wildcard_yaml' | 'lines' | 'image';

export interface PromptImportFormatOption {
	value: PromptImportFormatValue;
	label: string;
	hint: string;
}

/** The format select shown in the modal, in display order. Empty value = auto-detect. */
export const IMPORT_FORMAT_OPTIONS: PromptImportFormatOption[] = [
	{ value: '', label: 'Auto-detect', hint: 'Detected from the file extension, then from the content.' },
	{
		value: 'styles_csv',
		label: 'styles.csv',
		hint: 'A1111 / Forge / SD.Next style library (name, prompt, negative_prompt).'
	},
	{ value: 'style_json', label: 'Style JSON', hint: 'Fooocus-style JSON style list.' },
	{ value: 'wildcard_yaml', label: 'Wildcard YAML', hint: 'One wildcard file, each entry imported as a prompt.' },
	{ value: 'lines', label: 'One prompt per line', hint: 'Plain text, one prompt per non-empty line.' },
	{ value: 'image', label: 'Image metadata', hint: 'PNG, JPEG or WebP files carrying generation metadata.' }
];

/** File picker `accept` attribute for the drop zone / file input. */
export const IMPORT_ACCEPT = '.csv,.json,.yaml,.yml,.txt,.png,.jpg,.jpeg,.webp';

/** Human label for a format key as returned by the backend in the per-file result. */
export function importFormatLabel(format: string): string {
	const known = IMPORT_FORMAT_OPTIONS.find((option) => option.value === format);
	if (known) return known.label;
	return format || 'Unknown';
}

/** Copy for a per-file skip reason, as returned by `POST /api/prompts/import`.
 *  `reason` is either a known key (`no_metadata`, `empty`) or the raw backend
 *  message verbatim - unrecognized values are shown as-is. */
export function importSkipReasonCopy(reason: string | undefined): string | null {
	if (!reason) return null;
	if (reason === 'no_metadata') return 'No generation metadata found';
	if (reason === 'empty') return 'Empty';
	return reason;
}

export interface PromptImportFormInput {
	files: File[];
	pastedText: string;
	format: PromptImportFormatValue;
	modelId: string | null;
	baseModel: string;
}

/** Builds the multipart body for `POST /api/prompts/import`. Pasted text (if
 *  any) is sent as an extra file part so the endpoint's single `files` field
 *  covers both sources uniformly. */
export function buildPromptImportFormData(input: PromptImportFormInput): FormData {
	const formData = new FormData();
	for (const file of input.files) formData.append('files', file);

	const pastedText = input.pastedText.trim();
	if (pastedText) {
		formData.append('files', new File([pastedText], 'pasted.txt', { type: 'text/plain' }));
	}

	if (input.format) formData.append('format', input.format);
	if (input.modelId) formData.append('model_id', input.modelId);
	if (input.baseModel.trim()) formData.append('base_model', input.baseModel.trim());

	return formData;
}

/** Whether the form has anything to submit - at least one file or non-blank pasted text. */
export function hasPromptImportInput(input: Pick<PromptImportFormInput, 'files' | 'pastedText'>): boolean {
	return input.files.length > 0 || input.pastedText.trim().length > 0;
}
