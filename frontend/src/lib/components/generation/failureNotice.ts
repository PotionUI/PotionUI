export interface FailureSource {
	status?: string;
	error_message?: string | null;
	error_user_message?: string | null;
	error_contact_admin?: boolean;
	error_id?: string | null;
}

export interface FailureNoticeData {
	title: 'Failed' | 'Cancelled';
	message: string;
	errorId: string | null;
	contactAdmin: boolean;
	copyText: string;
}

export function failureNotice(source: FailureSource, isAdmin: boolean): FailureNoticeData | null {
	if (source.status !== 'failed' && source.status !== 'cancelled') return null;
	const title = source.status === 'failed' ? 'Failed' : 'Cancelled';
	const stored = source.error_user_message || source.error_message || '';
	const trusted = isAdmin || source.error_contact_admin === true;
	if (!trusted || !stored) {
		const message = source.status === 'failed' ? 'This generation failed.' : 'This generation was cancelled.';
		return { title, message, errorId: null, contactAdmin: false, copyText: message };
	}
	return {
		title,
		message: stored,
		errorId: source.error_id || null,
		contactAdmin: source.error_contact_admin === true,
		copyText: stored
	};
}
