import { describe, it, expect } from 'vitest';
import { curlSnippet, mcpClientConfig } from './apiKeySnippets';

describe('apiKeySnippets', () => {
	it('builds a curl call against the presets route', () => {
		expect(curlSnippet('https://potion.example.com', 'pui_abc')).toBe(
			'curl -H "Authorization: Bearer pui_abc" \\\n  https://potion.example.com/api/presets'
		);
	});

	it('builds an MCP client config that parses back with the key in the header', () => {
		const parsed = JSON.parse(mcpClientConfig('https://potion.example.com', 'pui_abc'));
		expect(parsed.mcpServers.potionui.url).toBe('https://potion.example.com/api/mcp');
		expect(parsed.mcpServers.potionui.headers.Authorization).toBe('Bearer pui_abc');
	});
});
