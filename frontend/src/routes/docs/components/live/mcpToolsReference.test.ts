import { describe, expect, it } from 'vitest';
import { matchesMcpTool, toolParameters, type McpToolEntry } from './mcpToolsReference';

const searchTool: McpToolEntry = {
	name: 'search_gallery',
	description: 'Search generated media by prompt, tags, and date.',
	group: 'Gallery',
	mutating: false,
	parameters: [
		{ name: 'query', type: 'string', required: true, description: 'Free-text search.' },
		{ name: 'limit', type: 'integer', required: false, description: 'Max results.' }
	]
};

const mutatingTool: McpToolEntry = {
	name: 'organize_gallery',
	description: 'Tag or move generated media.',
	group: 'Gallery',
	mutating: true,
	parameters: []
};

describe('toolParameters', () => {
	it('returns the entry parameters', () => {
		expect(toolParameters(searchTool)).toBe(searchTool.parameters);
	});

	it('defaults to an empty list when parameters is absent', () => {
		expect(toolParameters({ name: 'x' })).toEqual([]);
	});
});

describe('matchesMcpTool', () => {
	it('matches on the tool name', () => {
		expect(matchesMcpTool(searchTool, 'search_gal')).toBe(true);
	});

	it('matches on the description', () => {
		expect(matchesMcpTool(searchTool, 'prompt')).toBe(true);
	});

	it('matches on the group', () => {
		expect(matchesMcpTool(searchTool, 'gallery')).toBe(true);
	});

	it('matches on a parameter name, not just the name/description/group', () => {
		expect(matchesMcpTool(searchTool, 'limit')).toBe(true);
		expect(matchesMcpTool(mutatingTool, 'limit')).toBe(false);
	});

	it('is case-insensitive', () => {
		expect(matchesMcpTool(searchTool, 'QUERY')).toBe(true);
	});

	it('rejects a query matching neither the name, description, group nor any parameter', () => {
		expect(matchesMcpTool(searchTool, 'nonexistent')).toBe(false);
	});
});
