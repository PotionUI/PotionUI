export function formatMirrorIds(
	providerModelId?: string | null,
	providerVersionId?: string | null
): string {
	const parts: string[] = [];
	if (providerModelId) parts.push(`model ${providerModelId}`);
	if (providerVersionId) parts.push(`version ${providerVersionId}`);
	return parts.join(' · ');
}
