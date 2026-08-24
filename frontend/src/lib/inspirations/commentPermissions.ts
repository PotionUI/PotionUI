/**
 * Who may delete a given inspiration comment - the comment's own author, or
 * an admin. Mirrors the server-side rule stated in the API contract; the
 * server is still the authority, this only decides whether to render the
 * delete control.
 */

export interface InspirationCommentAuthorLike {
	user: { id: string };
}

export function canDeleteInspirationComment(
	comment: InspirationCommentAuthorLike,
	viewer: { id: string; account_type: string } | null | undefined
): boolean {
	if (!viewer) return false;
	return viewer.account_type === 'ADMIN' || viewer.id === comment.user.id;
}
