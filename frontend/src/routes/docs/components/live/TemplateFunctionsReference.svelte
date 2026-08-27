<script lang="ts">
	import { api } from '$lib/services/api/index';
	import { Badge } from '$lib/components/ui';
	import Icon from '$lib/components/Icon.svelte';
	import { copyText } from '$lib/utils/clipboard';
	import { toasts } from '$lib/stores/toast';
	import LiveReferenceDataShell from './LiveReferenceDataShell.svelte';

	interface FunctionParam {
		name: string;
		description?: string;
		default?: unknown;
		[key: string]: unknown;
	}

	interface FunctionExample {
		code: string;
		result: string;
		[key: string]: unknown;
	}

	interface TemplateFunction {
		name: string;
		alias?: string;
		category: string;
		description: string;
		signature: string;
		parameters?: FunctionParam[];
		examples?: FunctionExample[];
		[key: string]: unknown;
	}

	// Populated as a side effect of load() - the categories list comes back
	// alongside the functions in the same response.
	let categories: string[] = [];

	let copiedCode = '';
	let copyTimeout: ReturnType<typeof setTimeout> | null = null;

	async function copyToClipboard(text: string) {
		const ok = await copyText(text);
		if (ok) {
			copiedCode = text;
			if (copyTimeout) clearTimeout(copyTimeout);
			copyTimeout = setTimeout(() => {
				copiedCode = '';
			}, 1500);
		} else {
			toasts.error('Could not copy');
		}
	}

	function matches(func: TemplateFunction, query: string): boolean {
		const needle = query.toLowerCase();
		return (
			func.name.toLowerCase().includes(needle) ||
			func.description.toLowerCase().includes(needle) ||
			func.category.toLowerCase().includes(needle)
		);
	}

	async function load(): Promise<TemplateFunction[]> {
		const response = await api.getDeveloperTemplateFunctions();
		if (response.success && response.data) {
			categories = response.data.categories || [];
			return response.data.functions || [];
		}
		throw new Error(response.message || response.error || 'Failed to load template functions');
	}
</script>

<LiveReferenceDataShell {load} filter={matches} label="template functions">
	{#snippet content({ items })}
		{#each categories as category}
			{@const categoryFunctions = items.filter((func) => func.category === category)}
			{#if categoryFunctions.length > 0}
				<div class="bg-surface-2 rounded-lg border border-line shadow-raised mb-6">
					<div class="px-6 py-4 border-b border-line bg-surface-3">
						<h4 class="font-mono text-2xs uppercase tracking-[0.07em] text-fg-muted">{category}</h4>
					</div>
					<div class="divide-y divide-line">
						{#each categoryFunctions as func}
							<div class="px-6 py-5">
								<div class="flex items-start justify-between mb-3">
									<div>
										<h5 class="text-lg font-mono font-semibold text-fg">{func.name}()</h5>
										{#if func.alias}
											<p class="text-sm text-fg-subtle mt-1">
												Alias: <span class="font-mono text-fg-muted">{func.alias}()</span>
											</p>
										{/if}
									</div>
								</div>

								<p class="text-fg mb-4">{func.description}</p>

								<div class="bg-surface-3 rounded-lg p-4 mb-4">
									<p class="font-mono text-2xs uppercase tracking-[0.07em] text-fg-muted mb-2">
										Signature
									</p>
									<code class="text-sm text-fg font-mono">{func.signature}</code>
								</div>

								{#if func.parameters && func.parameters.length > 0}
									<div class="mb-4">
										<p class="text-sm font-semibold text-fg mb-2">Parameters:</p>
										<div class="space-y-2">
											{#each func.parameters as param}
												<div class="flex items-start gap-3">
													<Badge variant="info" class="font-mono">{param.name}</Badge>
													<div class="flex-1">
														<span class="text-sm text-fg-muted">{param.description}</span>
														{#if param.default}
															<span class="text-xs text-fg-subtle ml-2"
																>(default: {param.default})</span
															>
														{/if}
													</div>
												</div>
											{/each}
										</div>
									</div>
								{/if}

								{#if func.examples && func.examples.length > 0}
									<div>
										<p class="text-sm font-semibold text-fg mb-2">Examples:</p>
										<div class="space-y-3">
											{#each func.examples as example}
												<div class="bg-surface-3 rounded-lg p-4">
													<div class="flex items-start justify-between mb-2">
														<code class="text-sm text-success font-mono flex-1">{example.code}</code
														>
														<button
															on:click={() => copyToClipboard(example.code)}
															class="ml-2 p-1 hover:bg-surface-2 rounded transition-colors"
															title="Copy to clipboard"
														>
															<Icon name="copy" className="w-4 h-4 text-fg-subtle hover:text-fg" />
														</button>
													</div>
													<div class="text-xs text-fg-subtle">→ {example.result}</div>
													{#if copiedCode === example.code}
														<div class="text-xs text-success mt-1">Copied!</div>
													{/if}
												</div>
											{/each}
										</div>
									</div>
								{/if}
							</div>
						{/each}
					</div>
				</div>
			{/if}
		{/each}

		<div class="bg-surface-2 border border-line rounded-lg p-6">
			<h4
				class="font-mono text-2xs uppercase tracking-[0.07em] text-fg-muted flex items-center gap-2 pb-3 border-b border-line mb-3"
			>
				<Icon name="info" className="w-4 h-4" />
				Using Template Functions
			</h4>
			<div class="space-y-2 text-sm text-fg">
				<p>
					<strong>Template functions</strong> are used in pipeline.yml files to access configuration,
					form data, and settings.
				</p>
				<p class="mt-3">
					<strong>Context:</strong> Functions have access to preset data, form inputs, settings, and
					more.
				</p>
				<p>
					<strong>Jinja2 Syntax:</strong> Use double curly braces for output:
					<code class="font-mono bg-surface-1 px-2 py-1 rounded">{'{{ function_name() }}'}</code>
				</p>
				<p>
					<strong>Conditionals:</strong> Use
					<code class="font-mono bg-surface-1 px-2 py-1 rounded"
						>{'{% if condition %}...{% endif %}'}</code
					> for control flow.
				</p>
				<p class="mt-3"><strong>Tip:</strong> Click any example to copy it to your clipboard!</p>
			</div>
		</div>
	{/snippet}
</LiveReferenceDataShell>
