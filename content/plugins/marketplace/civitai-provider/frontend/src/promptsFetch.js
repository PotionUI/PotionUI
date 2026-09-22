export const USER_MODE_ALL = 'all';
export const USER_MODE_SELECTED = 'selected';

export function buildInitialFormState(options, currentUserId) {
	const defaults = options?.defaults || {};
	return {
		userMode: USER_MODE_SELECTED,
		selectedUserIds: currentUserId ? [currentUserId] : [],
		sort: defaults.sort ?? null,
		period: defaults.period ?? null,
		nsfw: defaults.nsfw ?? null,
		limit: defaults.limit ?? 1,
		includeShowcase: !!defaults.include_showcase,
		includeNegative: !!defaults.include_negative
	};
}

export function toggleSelectedUser(selectedUserIds, userId) {
	return selectedUserIds.includes(userId)
		? selectedUserIds.filter((id) => id !== userId)
		: [...selectedUserIds, userId];
}

export function clampLimit(rawValue, limitMax) {
	const max = Number.isFinite(limitMax) && limitMax > 0 ? limitMax : 200;
	const parsed = Math.trunc(Number(rawValue));
	if (!Number.isFinite(parsed)) return 1;
	return Math.min(Math.max(parsed, 1), max);
}

export function buildFetchRequestBody(modelId, formState) {
	return {
		model_ids: [modelId],
		user_ids: formState.userMode === USER_MODE_ALL ? null : formState.selectedUserIds,
		sort: formState.sort,
		period: formState.period,
		nsfw: formState.nsfw,
		limit: formState.limit,
		include_showcase: formState.includeShowcase,
		include_negative: formState.includeNegative
	};
}

export function isFormValid(formState) {
	if (formState.userMode === USER_MODE_ALL) return true;
	return formState.selectedUserIds.length > 0;
}

export function summarizeModelResult(modelResult, usersFetched) {
	const showPerUser = Array.isArray(usersFetched) && usersFetched.length > 1;
	const perUserRows = showPerUser && modelResult.per_user
		? Object.entries(modelResult.per_user).map(([userId, row]) => ({
				userId,
				created: row?.created ?? 0,
				skippedDuplicates: row?.skipped_duplicates ?? 0
			}))
		: [];

	return {
		modelId: modelResult.model_id,
		modelName: modelResult.model_name,
		notLinked: modelResult.error === 'model_not_linked',
		error: modelResult.error && modelResult.error !== 'model_not_linked' ? modelResult.error : null,
		imagesSeen: modelResult.images_seen ?? 0,
		withPrompt: modelResult.with_prompt ?? 0,
		created: modelResult.created ?? 0,
		skippedDuplicates: modelResult.skipped_duplicates ?? 0,
		perUserRows
	};
}

export function summarizeFetchResult(data) {
	const users = Array.isArray(data?.users) ? data.users : [];
	const models = Array.isArray(data?.models) ? data.models : [];
	return {
		users,
		models: models.map((m) => summarizeModelResult(m, users))
	};
}
