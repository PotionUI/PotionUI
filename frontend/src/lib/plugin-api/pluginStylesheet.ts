import { logger } from '$lib/utils/logger';

export const PLUGIN_SCOPE_ATTRIBUTE = 'data-potionui-plugin';

const GROUPING_RULES = new Set(['CSSMediaRule', 'CSSSupportsRule', 'CSSContainerRule', 'CSSLayerBlockRule']);
const KEPT_AT_RULES = new Set(['CSSKeyframesRule', 'CSSFontFaceRule']);

interface RuleList {
	cssRules: CSSRuleList;
	deleteRule(index: number): void;
}

function escapeRegex(text: string): string {
	return text.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

function quoted(text: string): string {
	return text.replace(/["\\]/g, '\\$&');
}

function scopePattern(pluginId: string): RegExp {
	const id = escapeRegex(pluginId);
	const dq = escapeRegex(quoted(pluginId));
	const bare = /^-?[A-Za-z_][\w-]*$/.test(pluginId) ? `|${id}` : '';
	return new RegExp(
		`^\\[\\s*${escapeRegex(PLUGIN_SCOPE_ATTRIBUTE)}\\s*=\\s*(?:"${dq}"|'${id}'${bare})\\s*\\](?:$|\\s*>\\s*(?![+~>])\\S|\\s+(?![+~>])\\S)`
	);
}

export function splitSelectorList(selectorText: string): string[] {
	const parts: string[] = [];
	let depth = 0;
	let quote = '';
	let start = 0;
	for (let i = 0; i < selectorText.length; i++) {
		const char = selectorText[i];
		if (quote) {
			if (char === '\\') i++;
			else if (char === quote) quote = '';
		} else if (char === '"' || char === "'") {
			quote = char;
		} else if (char === '\\') {
			i++;
		} else if (char === '(' || char === '[') {
			depth++;
		} else if (char === ')' || char === ']') {
			depth--;
		} else if (char === ',' && depth === 0) {
			parts.push(selectorText.slice(start, i).trim());
			start = i + 1;
		}
	}
	parts.push(selectorText.slice(start).trim());
	return parts;
}

export function pluginScopeSelector(pluginId: string): string {
	return `[${PLUGIN_SCOPE_ATTRIBUTE}="${quoted(pluginId)}"]`;
}

function staysInScope(selectorText: string, pattern: RegExp): boolean {
	return splitSelectorList(selectorText).every((selector) => pattern.test(selector));
}

function keepScopedRules(list: RuleList, pattern: RegExp): number {
	let dropped = 0;
	for (let i = list.cssRules.length - 1; i >= 0; i--) {
		const rule = list.cssRules[i];
		const kind = rule.constructor.name;
		if (kind === 'CSSStyleRule') {
			if (staysInScope((rule as CSSStyleRule).selectorText, pattern)) continue;
		} else if (GROUPING_RULES.has(kind)) {
			dropped += keepScopedRules(rule as unknown as RuleList, pattern);
			continue;
		} else if (KEPT_AT_RULES.has(kind)) {
			continue;
		}
		list.deleteRule(i);
		dropped++;
	}
	return dropped;
}

export function scopedPluginStylesheet(
	pluginId: string,
	cssText: string,
	baseURL: string
): { sheet: CSSStyleSheet; dropped: number } {
	const sheet = new CSSStyleSheet({ baseURL });
	sheet.replaceSync(cssText);
	const dropped = keepScopedRules(sheet, scopePattern(pluginId));
	return { sheet, dropped };
}

export async function loadPluginStylesheet(
	pluginId: string,
	url: string,
	fetchImpl: typeof fetch = fetch
): Promise<CSSStyleSheet | null> {
	try {
		const response = await fetchImpl(url);
		if (!response.ok) throw new Error(`HTTP ${response.status}`);
		const { sheet, dropped } = scopedPluginStylesheet(pluginId, await response.text(), url);
		if (dropped > 0) {
			logger.warn(`[plugin ${pluginId}] ${dropped} stylesheet rule(s) reach outside the plugin page and were dropped`);
		}
		return sheet;
	} catch (error) {
		logger.warn(`[plugin ${pluginId}] the stylesheet ${url} was not applied:`, error);
		return null;
	}
}

export function attachPluginStylesheet(doc: Document, sheet: CSSStyleSheet): () => void {
	if (Array.isArray(doc.adoptedStyleSheets)) {
		doc.adoptedStyleSheets = [...doc.adoptedStyleSheets, sheet];
		return () => {
			doc.adoptedStyleSheets = doc.adoptedStyleSheets.filter((adopted) => adopted !== sheet);
		};
	}
	const style = doc.createElement('style');
	style.textContent = Array.from(sheet.cssRules, (rule) => rule.cssText).join('\n');
	doc.head.appendChild(style);
	return () => style.remove();
}
