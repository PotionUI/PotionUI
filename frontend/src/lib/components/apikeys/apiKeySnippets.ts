export function curlSnippet(origin: string, secret: string): string {
	return `curl -H "Authorization: Bearer ${secret}" \\\n  ${origin}/api/presets`;
}

export function mcpClientConfig(origin: string, secret: string): string {
	return JSON.stringify(
		{ mcpServers: { potionui: { url: `${origin}/api/mcp`, headers: { Authorization: `Bearer ${secret}` } } } },
		null,
		2
	);
}
