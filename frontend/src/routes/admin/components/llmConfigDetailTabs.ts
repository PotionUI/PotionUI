export type LLMConfigDetailTab = 'configuration' | 'toolset' | 'access';

export function llmConfigDetailTabHasFooter(tab: LLMConfigDetailTab): boolean {
	return tab === 'configuration';
}
