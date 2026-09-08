<script lang="ts">
	import { onMount } from 'svelte';
	import { api } from '$lib/services/api/index';
	import { Badge, Button } from '$lib/components/ui';
	import { authStore } from '$lib/stores/auth';
	import { adminWebSocket } from '$lib/services/adminWebsocket';
	import { logger } from '$lib/utils/logger';
	import LiveReferenceDataShell from './LiveReferenceDataShell.svelte';
	import {
		groupPipesByFamily,
		ioChipLabel,
		ioChipTitle,
		matchesPipe,
		pipeConfiguration,
		pipeDescription,
		pipeFamilyAnchor,
		pipeInputs,
		pipeKey,
		pipeLabel,
		pipeOutputs,
		pipeVariant,
		pipeVariantLabel,
		type PipeEntry
	} from './pipesReference';

	// `$state`, not a plain `let`: the install affordance below puts this
	// component in runes mode, where a plain `let` no longer repaints.
	let configExpanded = $state<Record<string, boolean>>({});
	// What the install of a pipe has reported since this page loaded. Overlays
	// the status the reference was fetched with, which cannot update itself.
	let live = $state<Record<string, { status: string; message: string | null }>>({});

	let isAdmin = $derived($authStore.user?.account_type === 'ADMIN');

	const STATUS_LABELS: Record<string, string> = {
		installed: 'installed',
		installing: 'installing',
		not_installed: 'not installed',
		error: 'install failed'
	};

	const STATUS_VARIANTS: Record<string, 'success' | 'info' | 'warning' | 'danger'> = {
		installed: 'success',
		installing: 'info',
		not_installed: 'warning',
		error: 'danger'
	};

	function statusOf(pipe: PipeEntry): string {
		return live[pipeLabel(pipe)]?.status ?? String(pipe.status ?? '');
	}

	function messageOf(pipe: PipeEntry): string | null {
		return live[pipeLabel(pipe)]?.message ?? null;
	}

	function toggleConfig(key: string) {
		configExpanded = { ...configExpanded, [key]: !configExpanded[key] };
	}

	async function install(pipe: PipeEntry) {
		const name = pipeLabel(pipe);
		live = { ...live, [name]: { status: 'installing', message: 'Starting install...' } };
		try {
			const response = await api.installPipe(name);
			if (!response.success) {
				throw new Error(response.message || response.error || 'Install failed');
			}
		} catch (err) {
			// A refusal carries the commands that do work (422) - show those
			// rather than a generic failure.
			const detail = (err as { response?: { data?: { detail?: unknown } } })?.response?.data
				?.detail;
			live = {
				...live,
				[name]: {
					status: 'error',
					message:
						typeof detail === 'string'
							? detail
							: err instanceof Error
								? err.message
								: 'Install failed'
				}
			};
		}
	}

	async function load(): Promise<PipeEntry[]> {
		const response = await api.getDocsLivePipes();
		if (response.success && response.data) {
			const raw = response.data;
			return Array.isArray(raw) ? raw : raw.pipes || [];
		}
		throw new Error(response.message || response.error || 'Failed to load pipes reference');
	}

	onMount(() => {
		const unsubscribe = adminWebSocket.onPipeInstallStatus(({ pipe, status, message }) => {
			live = { ...live, [pipe]: { status, message } };
		});

		if (isAdmin && !adminWebSocket.isConnected()) {
			adminWebSocket.connectAsync().catch((err) => {
				logger.error('Admin WebSocket unavailable - install progress will not stream:', err);
			});
		}

		// The socket is a shared singleton; only this subscription is ours to drop.
		return unsubscribe;
	});
</script>

<LiveReferenceDataShell {load} filter={matchesPipe} label="pipes">
	{#snippet content({ items })}
		{@const groups = groupPipesByFamily(items)}

		{#if groups.length > 1}
			<div class="flex flex-wrap gap-1.5 mb-4">
				{#each groups as group (group.family)}
					<a
						href="#{pipeFamilyAnchor(group.family)}"
						class="inline-flex items-center gap-1 rounded border border-line-strong bg-surface-2 px-1.5 py-0.5 font-mono text-2xs text-fg-muted hover:border-line-hover hover:text-fg transition-colors"
					>
						{group.family}
						<span class="text-fg-subtle">{group.pipes.length}</span>
					</a>
				{/each}
			</div>
		{/if}

		<div class="space-y-6">
			{#each groups as group (group.family)}
				{@const hasVariants = group.pipes.some((p) => pipeVariant(p) !== undefined)}
				<div id={pipeFamilyAnchor(group.family)} class="scroll-mt-2">
					<h3
						class="flex items-baseline gap-2 text-xs font-semibold uppercase tracking-wide text-fg-muted mb-2"
					>
						<code class="font-mono normal-case">{group.family}</code>
						{#if hasVariants}
							<span class="text-fg-subtle normal-case tabular-nums">
								{group.pipes.length} variant{group.pipes.length === 1 ? '' : 's'}
							</span>
						{/if}
					</h3>

					<div class="space-y-3">
						{#each group.pipes as pipe, index (pipeKey(pipe, index))}
							{@const key = pipeKey(pipe, index)}
							{@const status = statusOf(pipe)}
							{@const variantLabel = pipeVariantLabel(pipe)}
							{@const description = pipeDescription(pipe)}
							{@const inputs = pipeInputs(pipe)}
							{@const outputs = pipeOutputs(pipe)}
							{@const configuration = pipeConfiguration(pipe)}
							<div class="border border-line rounded-lg bg-surface-1">
								<div class="flex flex-wrap items-center gap-2 px-4 py-3 border-b border-line">
									<code class="text-sm font-mono font-semibold text-fg">{pipeLabel(pipe)}</code>
									{#if variantLabel}
										<span class="text-xs text-fg-subtle">{variantLabel}</span>
									{/if}
									{#if status && status !== 'installed'}
										<Badge size="sm" variant={STATUS_VARIANTS[status] ?? 'neutral'}>
											{STATUS_LABELS[status] ?? status}
										</Badge>
									{/if}
									{#if pipe.manual_install}
										<Badge size="sm" variant="neutral">manual setup</Badge>
									{/if}
								</div>

								<div class="px-4 py-3 space-y-3">
									{#if description}
										<p class="text-sm text-fg-muted">{description}</p>
									{/if}

									{#if status !== 'installed' || pipe.manual_install}
										<div class="space-y-2">
											<h4 class="text-2xs font-semibold uppercase tracking-wide text-fg-subtle">
												Installation
											</h4>

											{#if pipe.manual_install}
												<p class="text-xs text-fg-muted">
													This pipe's requirements are built on this machine, not fetched from a
													package index - nothing here can install them for you. Run these, then
													reload:
												</p>
												<pre
													class="text-xs font-mono bg-surface-2 border border-warning/25 rounded p-2 overflow-x-auto text-fg whitespace-pre-wrap">{pipe.manual_install}</pre>
											{:else if isAdmin}
												<div class="flex items-center gap-2">
													<Button
														size="xs"
														onclick={() => install(pipe)}
														loading={status === 'installing'}
														disabled={status === 'installing'}
													>
														{status === 'error' ? 'Retry install' : 'Install requirements'}
													</Button>
													{#if pipe.requirements !== undefined}
														<span class="text-xs text-fg-subtle">runs pip / git on the server</span>
													{/if}
												</div>
											{:else}
												<p class="text-xs text-fg-muted">
													An administrator has to install this pipe's requirements.
												</p>
											{/if}

											{#if messageOf(pipe)}
												<pre
													class="text-xs font-mono bg-surface-2 rounded p-2 overflow-x-auto whitespace-pre-wrap {status ===
													'error'
														? 'text-danger'
														: 'text-fg-muted'}">{messageOf(pipe)}</pre>
											{/if}
										</div>
									{/if}

									{#if inputs.length > 0}
										<div>
											<h4 class="text-2xs font-semibold uppercase tracking-wide text-fg-subtle mb-1">
												Inputs
											</h4>
											<div class="flex flex-wrap gap-1.5">
												{#each inputs as input, i (`${input.name}:${i}`)}
													<span
														class="inline-flex items-center gap-1 rounded border border-line-strong bg-surface-2 px-1.5 py-0.5 font-mono text-2xs text-fg-muted"
														title={ioChipTitle(input, input.required)}
													>
														{#if input.required}
															<span class="w-1 h-1 rounded-full bg-warning" aria-hidden="true"></span>
														{/if}
														<span class="text-fg">{ioChipLabel(input)}</span>
														<span class="text-fg-subtle">· {input.io_type}</span>
													</span>
												{/each}
											</div>
										</div>
									{/if}

									{#if outputs.length > 0}
										<div>
											<h4 class="text-2xs font-semibold uppercase tracking-wide text-fg-subtle mb-1">
												Outputs
											</h4>
											<div class="flex flex-wrap gap-1.5">
												{#each outputs as output, i (`${output.name}:${i}`)}
													<span
														class="inline-flex items-center gap-1 rounded border border-line-strong bg-surface-2 px-1.5 py-0.5 font-mono text-2xs text-fg-muted"
														title={ioChipTitle(output)}
													>
														<span class="text-fg">{ioChipLabel(output)}</span>
														<span class="text-fg-subtle">· {output.io_type}</span>
													</span>
												{/each}
											</div>
										</div>
									{/if}

									{#if configuration.length > 0}
										<div>
											<button
												type="button"
												class="w-full flex items-center gap-2 text-left"
												aria-expanded={!!configExpanded[key]}
												onclick={() => toggleConfig(key)}
											>
												<h4 class="text-2xs font-semibold uppercase tracking-wide text-fg-subtle">
													Configuration ({configuration.length})
												</h4>
												<svg
													class="w-3 h-3 flex-shrink-0 text-fg-subtle transition-transform {configExpanded[
														key
													]
														? 'rotate-180'
														: ''}"
													fill="none"
													stroke="currentColor"
													viewBox="0 0 24 24"
												>
													<path
														stroke-linecap="round"
														stroke-linejoin="round"
														stroke-width="2"
														d="M19 9l-7 7-7-7"
													/>
												</svg>
											</button>

											{#if configExpanded[key]}
												<div class="overflow-x-auto mt-2">
													<table class="w-full text-sm">
														<thead>
															<tr class="text-left text-fg-subtle text-xs">
																<th class="pr-3 pb-1 font-medium">Option</th>
																<th class="pr-3 pb-1 font-medium">Type</th>
																<th class="pr-3 pb-1 font-medium">Default</th>
																<th class="pb-1 font-medium">Description</th>
															</tr>
														</thead>
														<tbody>
															{#each configuration as option, i (`${option.name}:${i}`)}
																<tr class="border-t border-line/60 align-top">
																	<td class="pr-3 py-1.5 font-mono text-fg whitespace-nowrap">
																		{option.name}
																		{#if option.required}<Badge
																				variant="warning"
																				size="sm"
																				class="ml-1">required</Badge
																			>{/if}
																	</td>
																	<td class="pr-3 py-1.5 font-mono text-fg-muted whitespace-nowrap"
																		>{option.param_type ?? '—'}</td
																	>
																	<td
																		class="pr-3 py-1.5 font-mono tabular-nums text-fg-muted whitespace-nowrap"
																	>
																		{option.default !== undefined && option.default !== null
																			? JSON.stringify(option.default)
																			: '—'}
																	</td>
																	<td class="py-1.5 text-fg-muted">
																		{option.description || ''}
																		{#if option.choices && option.choices.length > 0}
																			<div class="mt-1 flex flex-wrap gap-1">
																				{#each option.choices as choice}
																					<code
																						class="text-2xs font-mono bg-surface-2 border border-line rounded px-1 py-0.5"
																						>{choice}</code
																					>
																				{/each}
																			</div>
																		{/if}
																		{#if option.min_value !== null && option.min_value !== undefined && option.max_value !== null && option.max_value !== undefined}
																			<div class="mt-1 text-xs text-fg-subtle">
																				{option.min_value}..{option.max_value}
																			</div>
																		{/if}
																	</td>
																</tr>
															{/each}
														</tbody>
													</table>
												</div>
											{/if}
										</div>
									{/if}

									{#if pipe.requirements !== undefined}
										<div>
											<h4 class="text-2xs font-semibold uppercase tracking-wide text-fg-subtle mb-1">
												Requirements
											</h4>
											<pre
												class="text-xs font-mono bg-surface-2 rounded p-2 overflow-x-auto text-fg-muted">{JSON.stringify(
													pipe.requirements,
													null,
													2
												)}</pre>
										</div>
									{/if}

									{#if inputs.length === 0 && outputs.length === 0 && configuration.length === 0 && pipe.requirements === undefined}
										<p class="text-xs text-fg-subtle">No additional spec available for this pipe.</p>
									{/if}
								</div>
							</div>
						{/each}
					</div>
				</div>
			{/each}
		</div>
	{/snippet}
</LiveReferenceDataShell>
