/**
 * Validation for the "Publish to Inspirations" dialog (PublishToInspirationsModal).
 * Pure and side-effect free so it stays trivially unit-testable.
 */

export interface PublishFormInput {
	title: string;
	/** Every output filename the source generation has, in display order. */
	availableFilenames: string[];
	/** The subset the user has checked to publish. */
	selectedFilenames: string[];
}

export interface PublishFormValidation {
	valid: boolean;
	error?: string;
}

const MAX_TITLE_LENGTH = 200;

export function validatePublishForm(input: PublishFormInput): PublishFormValidation {
	const title = input.title.trim();
	if (!title) {
		return { valid: false, error: 'Title is required.' };
	}
	if (title.length > MAX_TITLE_LENGTH) {
		return { valid: false, error: `Title must be ${MAX_TITLE_LENGTH} characters or fewer.` };
	}
	// A single-output generation has nothing to choose between - only a
	// multi-output one needs at least one file actually checked.
	if (input.availableFilenames.length > 1 && input.selectedFilenames.length === 0) {
		return { valid: false, error: 'Select at least one file to publish.' };
	}
	return { valid: true };
}
