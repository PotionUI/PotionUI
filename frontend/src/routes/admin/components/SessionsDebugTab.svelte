<script lang="ts">
	import Card from '$lib/components/ui/Card.svelte';
	import Tooltip from '$lib/components/Tooltip.svelte';
	import { logger } from '$lib/utils/logger';
	import { onMount } from 'svelte';
	import { page } from '$app/stores';
	import { goto } from '$app/navigation';
	import * as adminApi from '$lib/services/admin-api';
	import type {
		AdminChatSessionSummary,
		AdminChatSessionDetailResult,
		AdminChatCallTrace,
		AdminChatMessage,
		AdminChatToolExecution,
		AdminChatBehaviorTrace
	} from '$lib/services/admin-api';
	import { toasts } from '$lib/stores/toast';
	import { confirmDialog } from '$lib/stores/confirm';
	import { debounce } from '$lib/stores/tabPersistence';
	import { timeAgo } from '$lib/utils/relativeTime';
	import Icon from '$lib/components/Icon.svelte';
	import { Button, Badge, EmptyState, Spinner, Alert } from '$lib/components/ui';
	import MasterDetailLayout from '$lib/components/master-detail/MasterDetailLayout.svelte';
	import { Pane, PaneRow, PanePager } from '$lib/components/pane';
	import LibraryFilterBar from '$lib/components/library/LibraryFilterBar.svelte';
	import AdminTabShell from './AdminTabShell.svelte';
	import {
		SESSIONS_SORT_OPTIONS,
		sessionsFiltersFromSearchParams,
		sessionsFiltersToSearchParams,
		type SessionsFilters
	} from './sessionsFilters';

	const PAGE_SIZE = 20;
	const FILTER_PARAM_KEYS = ['q', 'sort_by'];

	let sessions: AdminChatSessionSummary[] = [];
	let total = 0;
	let offset = 0;
	let listLoading = true;
	let listError: string | null = null;
	let tracingEnabled = true;
	let lastFiltersKey = '';

	let selectedSessionId: string | null = null;
	let detail: AdminChatSessionDetailResult | null = null;
	let detailLoading = false;
	let detailError: string | null = null;

	let expandedTraces: Record<string, boolean> = {};

	let expandedSections: Record<string, boolean> = {};

	let clearingScope: 'session' | 'all' | 'sessions' | null = null;

	$: filters = sessionsFiltersFromSearchParams($page.url.searchParams);
	$: filtersKey = JSON.stringify(filters);

	$: {
		if (filtersKey !== lastFiltersKey) {
			lastFiltersKey = filtersKey;
			offset = 0;
			loadSessions();
		}
	}

	async function loadSessions() {
		listLoading = true;
		listError = null;
		try {
			const response = await adminApi.getAdminChatSessions(filters.q, PAGE_SIZE, offset);
			if (response.success && response.data) {
				sessions = response.data.sessions;
				total = response.data.total;
				tracingEnabled = response.data.tracing_enabled;
			} else {
				listError = response.message || 'Failed to load sessions';
			}
		} catch (e: any) {
			listError = e.response?.data?.message || e.message || 'Failed to load sessions';
		} finally {
			listLoading = false;
		}
	}

	function applyFilters(next: SessionsFilters) {
		const url = new URL($page.url);
		for (const key of FILTER_PARAM_KEYS) url.searchParams.delete(key);
		for (const [key, value] of sessionsFiltersToSearchParams(next)) url.searchParams.set(key, value);
		void goto(url, { replaceState: true, keepFocus: true, noScroll: true });
	}

	const debouncedApplyFilters = debounce(applyFilters, 300);

	function onQueryChange(value: string) {
		debouncedApplyFilters({ ...filters, q: value });
	}

	function onSortChange(value: string) {
		applyFilters({ ...filters, sortBy: value as SessionsFilters['sortBy'] });
	}

	function nextPage() {
		if (offset + PAGE_SIZE >= total) return;
		offset += PAGE_SIZE;
		loadSessions();
	}

	function prevPage() {
		if (offset === 0) return;
		offset = Math.max(0, offset - PAGE_SIZE);
		loadSessions();
	}

	async function selectSession(sessionId: string) {
		selectedSessionId = sessionId;
		detail = null;
		detailError = null;
		expandedTraces = {};
		detailLoading = true;
		try {
			const response = await adminApi.getAdminChatSessionDetail(sessionId);
			if (response.success && response.data) {
				detail = response.data;
			} else {
				detailError = response.message || 'Failed to load session detail';
			}
		} catch (e: any) {
			detailError = e.response?.data?.message || e.message || 'Failed to load session detail';
		} finally {
			detailLoading = false;
		}
	}

	function toggleTrace(traceId: string) {
		expandedTraces = { ...expandedTraces, [traceId]: !expandedTraces[traceId] };
	}

	function toggleSection(key: string) {
		expandedSections = { ...expandedSections, [key]: !expandedSections[key] };
	}

	function tracesForMessage(messageId: string): AdminChatCallTrace[] {
		if (!detail) return [];
		return detail.traces.filter((t) => t.message_id === messageId);
	}

	$: unattributedTraces = detail ? detail.traces.filter((t) => t.message_id === null) : [];
	$: sessionHasNoTraces = detail ? detail.traces.length === 0 : false;

	async function clearSessionTraces() {
		if (!selectedSessionId) return;
		const confirmed = await confirmDialog({
			title: 'Clear all LLM call traces for this session?',
			message: 'This cannot be undone.',
			variant: 'danger'
		});
		if (!confirmed) return;
		clearingScope = 'session';
		try {
			const result = await adminApi.clearChatCallTraces(selectedSessionId);
			if (result.success) {
				toasts.success(`Cleared ${result.data?.deleted ?? 0} trace(s) for this session`);
				await selectSession(selectedSessionId);
			} else {
				toasts.error(result.message || 'Failed to clear traces');
			}
		} catch (e: any) {
			logger.error('Failed to clear session traces:', e);
			toasts.error(e.response?.data?.message || e.message || 'Failed to clear traces');
		} finally {
			clearingScope = null;
		}
	}

	async function clearAllTraces() {
		const confirmed = await confirmDialog({
			title: 'Clear LLM call traces for ALL sessions?',
			message: 'This cannot be undone.',
			variant: 'danger'
		});
		if (!confirmed) return;
		clearingScope = 'all';
		try {
			const result = await adminApi.clearChatCallTraces();
			if (result.success) {
				toasts.success(`Cleared ${result.data?.deleted ?? 0} trace(s)`);
				if (selectedSessionId) await selectSession(selectedSessionId);
			} else {
				toasts.error(result.message || 'Failed to clear traces');
			}
		} catch (e: any) {
			logger.error('Failed to clear all traces:', e);
			toasts.error(e.response?.data?.message || e.message || 'Failed to clear traces');
		} finally {
			clearingScope = null;
		}
	}

	async function clearAllSessions() {
		const confirmed = await confirmDialog({
			title: 'Delete ALL chat sessions?',
			message: `Every conversation of every user will be deleted. Memory notes stay. This cannot be undone.`,
			variant: 'danger'
		});
		if (!confirmed) return;
		clearingScope = 'sessions';
		try {
			const result = await adminApi.clearAdminChatSessions();
			if (result.success) {
				toasts.success(`Deleted ${result.data?.deleted ?? 0} session(s)`);
				selectedSessionId = null;
				detail = null;
				await loadSessions();
			} else {
				toasts.error(result.message || 'Failed to clear chat sessions');
			}
		} catch (e: any) {
			logger.error('Failed to clear chat sessions:', e);
			toasts.error(e.response?.data?.message || e.message || 'Failed to clear chat sessions');
		} finally {
			clearingScope = null;
		}
	}

	function pretty(value: unknown): string {
		if (value === null || value === undefined) return '';
		if (typeof value === 'string') return value;
		try {
			return JSON.stringify(value, null, 2);
		} catch {
			return String(value);
		}
	}
</script>

<div class="flex min-h-[calc(100dvh-var(--header-h)-2rem)] flex-col gap-4 sm:min-h-[calc(100dvh-var(--header-h)-3rem)]">
	{#if !listLoading && !tracingEnabled}
		<div
			class="flex-shrink-0 flex items-center gap-2 rounded-lg border border-warning bg-warning/10 px-3 py-2 text-sm text-warning"
		>
			<Icon name="warning" className="w-4 h-4 flex-shrink-0" />
			<span>
				LLM call tracing is disabled — new chat turns won't be recorded. Enable the
				<span class="font-mono text-xs">chat_llm_call_tracing</span> setting in System Settings to
				capture wire-level traces going forward.
			</span>
		</div>
	{/if}

	<AdminTabShell
		title="Chat Sessions"
		icon="chat"
		counts={[{ label: total === 1 ? 'session' : 'sessions', value: total }]}
	>
		{#snippet actions()}
			<Button
				variant="danger"
				size="sm"
				loading={clearingScope === 'all'}
				onclick={clearAllTraces}
			>
				Clear all traces
			</Button>
			<Button
				variant="danger"
				size="sm"
				loading={clearingScope === 'sessions'}
				disabled={total === 0}
				onclick={clearAllSessions}
			>
				Clear chat history
			</Button>
		{/snippet}
	</AdminTabShell>

	<Card padding="none" class="flex flex-wrap items-center gap-2 px-4 py-2.5">
		<LibraryFilterBar
			q={filters.q}
			{onQueryChange}
			searchPlaceholder="Search name, user, email…"
			sortBy={filters.sortBy}
			sortOptions={SESSIONS_SORT_OPTIONS}
			{onSortChange}
		/>
	</Card>

	<section class="flex flex-1 flex-col rounded-lg border border-line bg-surface-1 overflow-hidden">
		<MasterDetailLayout leftWidth={320} minWidth={280} maxWidth={440} storageKey="admin-chat-sessions-width">
			<div slot="list" class="h-full min-h-0">
				<Pane
					label="Sessions"
					count={total}
					loading={listLoading}
					isEmpty={!listLoading && (Boolean(listError) || sessions.length === 0)}
					bodyRole="listbox"
					ariaLabel="Chat sessions"
				>
					{#snippet empty()}
						<div class="p-4 h-full flex items-center justify-center">
							{#if listError}
								<EmptyState title="Could not load sessions" description={listError} icon="warning" compact />
							{:else}
								<EmptyState
									icon="chat"
									title={filters.q.trim() ? 'No sessions match your search' : 'No chat sessions yet'}
									description={filters.q.trim()
										? 'Try a different name, user, or email.'
										: 'Chat sessions show up here once someone starts a conversation.'}
									compact
								/>
							{/if}
						</div>
					{/snippet}

					{#snippet children()}
						{#each sessions as session (session.id)}
							{#snippet sessionRow()}
								<div class="flex items-center justify-between gap-2 mb-1">
									<span class="text-sm font-medium truncate text-fg">{session.name || 'Untitled'}</span>
									<Badge variant="neutral" size="sm" class="font-mono uppercase flex-shrink-0">
										{session.mode}
									</Badge>
								</div>
								<div class="flex items-center justify-between gap-2 text-xs text-fg-subtle">
									<Tooltip text={session.email} wrapperClass="flex min-w-0"><span class="truncate">{session.username}</span></Tooltip>
									<span class="font-mono tabular-nums flex-shrink-0">
										{timeAgo(session.updated_at)}
									</span>
								</div>
								<div class="text-xs font-mono tabular-nums text-fg-subtle mt-0.5">
									{session.message_count} msg{session.message_count === 1 ? '' : 's'}
								</div>
							{/snippet}
							<PaneRow
								selected={selectedSessionId === session.id}
								onclick={() => selectSession(session.id)}
								children={sessionRow}
							/>
						{/each}
					{/snippet}

					{#snippet footer()}
						<PanePager {offset} limit={PAGE_SIZE} {total} onPrev={prevPage} onNext={nextPage} />
					{/snippet}
				</Pane>
			</div>

			<div slot="detail" class="h-full min-h-0 flex flex-col overflow-y-auto">
				{#if !selectedSessionId}
					<div class="flex-1 p-5 flex items-center justify-center">
						<EmptyState
							icon="chat"
							title="Select a session"
							description="Choose a session from the list to inspect its LLM calls."
							compact
						/>
					</div>
				{:else if detailLoading}
					<div class="flex-1 flex items-center justify-center">
						<Spinner size="lg" />
					</div>
				{:else if detailError}
					<div class="flex-1 p-5 flex items-center justify-center">
						<EmptyState title="Could not load session" description={detailError} icon="warning" compact />
					</div>
				{:else if detail}
					<div class="p-4 sm:p-5 space-y-4">
						<div class="flex items-start justify-between gap-3 pb-3 border-b border-line">
							<div>
								<h2 class="text-base font-semibold text-fg">{detail.session.name || 'Untitled'}</h2>
								<div class="flex items-center gap-2 mt-1 text-xs text-fg-subtle">
									<span>{detail.session.username} ({detail.session.email})</span>
									<span>·</span>
									<Badge variant="neutral" size="sm" class="font-mono uppercase">
										{detail.session.mode}
									</Badge>
									<span>·</span>
									<Badge variant="neutral" size="sm" class="uppercase">{detail.session.status}</Badge>
								</div>
							</div>
							<Button
								variant="danger"
								size="sm"
								loading={clearingScope === 'session'}
								onclick={clearSessionTraces}
							>
								Clear this session's traces
							</Button>
						</div>

						{#if sessionHasNoTraces}
							<p class="text-xs text-fg-subtle">
								No wire-level call traces for this session — traces are recorded only for turns
								sent while call tracing is enabled.
							</p>
						{/if}

						{#each detail.session.messages as message (message.id)}
							{@const traces = tracesForMessage(message.id)}
							{@const metadata = message.metadata}
							<div class="border border-line rounded-lg overflow-hidden">
								<div class="px-3 py-2 bg-surface-2 flex items-center justify-between">
									<Badge variant={message.role === 'user' ? 'signal' : 'neutral'} size="sm" class="uppercase">
										{message.role}
									</Badge>
									<span class="text-xs font-mono tabular-nums text-fg-subtle">
										{message.created_at}
									</span>
								</div>
								<div class="px-3 py-2">
									<pre class="text-sm text-fg whitespace-pre-wrap font-mono">{message.content}</pre>
								</div>

								{#if message.role === 'assistant' && metadata}
									<div class="px-3 pb-3 space-y-2">
										{@render metadataSection(message, metadata)}
									</div>
								{/if}

								{#if traces.length > 0}
									<div class="px-3 pb-3 space-y-2">
										{#each traces as trace (trace.id)}
											{@render traceCard(trace)}
										{/each}
									</div>
								{/if}
							</div>
						{/each}

						{#if unattributedTraces.length > 0}
							<div>
								<h3 class="text-xs font-mono uppercase tracking-[0.07em] text-fg-subtle mb-2">
									Unattributed calls
								</h3>
								<div class="space-y-2">
									{#each unattributedTraces as trace (trace.id)}
										{@render traceCard(trace)}
									{/each}
								</div>
							</div>
						{/if}
					</div>
				{/if}
			</div>
		</MasterDetailLayout>
	</section>
</div>

{#snippet metadataSection(message: AdminChatMessage, metadata: NonNullable<AdminChatMessage['metadata']>)}
	{@const toolExecutions = metadata.tool_executions ?? []}
	{@const behaviorTrace = metadata.behavior_trace}
	{@const toolsKey = `${message.id}:tools`}
	{@const traceKey = `${message.id}:trace`}
	{@const toolsOpen = !!expandedSections[toolsKey]}
	{@const traceOpen = !!expandedSections[traceKey]}

	{#if metadata.model || metadata.tokens_used != null || metadata.prompt_tokens != null}
		<div class="text-xs font-mono tabular-nums text-fg-subtle flex flex-wrap items-center gap-x-2">
			{#if metadata.model}<span>{metadata.model}</span>{/if}
			{#if metadata.prompt_tokens != null || metadata.completion_tokens != null}
				<span>{metadata.prompt_tokens ?? '?'}→{metadata.completion_tokens ?? '?'} tok</span>
			{:else if metadata.tokens_used != null}
				<span>{metadata.tokens_used} tok</span>
			{/if}
		</div>
	{/if}

	{#if toolExecutions.length > 0}
		<div class="border border-line-strong rounded bg-surface-2 overflow-hidden">
			<button
				type="button"
				class="w-full flex items-center gap-2 px-2.5 py-1.5 text-left hover:bg-surface-3 transition-colors"
				onclick={() => toggleSection(toolsKey)}
			>
				<Icon
					name={toolsOpen ? 'chevron-down' : 'chevron-right'}
					className="w-3.5 h-3.5 text-fg-subtle flex-shrink-0"
				/>
				<span class="text-xs font-mono text-fg-muted truncate">
					Tool executions ({toolExecutions.length})
				</span>
			</button>
			{#if toolsOpen}
				<div class="px-2.5 pb-2.5 space-y-2 border-t border-line">
					{#each toolExecutions as te, i (i)}
						<div class="mt-2">
							<div class="flex items-center gap-2 mb-1">
								<Badge variant="neutral" size="sm" class="font-mono">{te.tool_name}</Badge>
								<span class="text-xs font-mono tabular-nums text-fg-subtle">{te.duration_ms}ms</span>
								{#if te.result && !te.result.success}
									<Badge variant="danger" size="sm">failed</Badge>
								{/if}
								{#if te.pending_approval}
									<Badge variant="warning" size="sm">pending approval</Badge>
								{/if}
							</div>
							<pre
								class="text-xs font-mono whitespace-pre-wrap overflow-x-auto overflow-y-auto max-h-32 bg-surface-1 border border-line rounded p-2 text-fg-muted">{pretty(te.arguments)}</pre>
							<pre
								class="text-xs font-mono whitespace-pre-wrap overflow-x-auto overflow-y-auto max-h-32 bg-surface-1 border border-line rounded p-2 text-fg-muted mt-1">{pretty(te.result?.error ?? te.result?.data)}</pre>
						</div>
					{/each}
				</div>
			{/if}
		</div>
	{/if}

	{#if behaviorTrace}
		<div class="border border-line-strong rounded bg-surface-2 overflow-hidden">
			<button
				type="button"
				class="w-full flex items-center gap-2 px-2.5 py-1.5 text-left hover:bg-surface-3 transition-colors"
				onclick={() => toggleSection(traceKey)}
			>
				<Icon
					name={traceOpen ? 'chevron-down' : 'chevron-right'}
					className="w-3.5 h-3.5 text-fg-subtle flex-shrink-0"
				/>
				<span class="text-xs font-mono text-fg-muted truncate">
					Behavior trace · {behaviorTrace.system_prompt_source} ·
					{behaviorTrace.steps.reduce((sum, s) => sum + s.duration_ms, 0)}ms
				</span>
			</button>
			{#if traceOpen}
				<div class="px-2.5 pb-2.5 space-y-3 border-t border-line">
					<div class="mt-2 flex flex-wrap gap-1.5 text-xs">
						<Badge variant="neutral" size="sm" class="font-mono">mode: {behaviorTrace.mode ?? '—'}</Badge>
						<Badge variant="neutral" size="sm" class="font-mono">
							system: {behaviorTrace.system_prompt_source}
						</Badge>
					</div>

					{#if behaviorTrace.steps.length > 0}
						<div>
							<p class="text-xs font-mono uppercase tracking-[0.05em] text-fg-subtle mb-1">Steps</p>
							<div class="space-y-1">
								{#each behaviorTrace.steps as step, i (i)}
									<div class="flex items-center justify-between text-xs font-mono text-fg-muted">
										<span>{step.step}</span>
										<span class="tabular-nums text-fg-subtle">{step.duration_ms}ms</span>
									</div>
								{/each}
							</div>
						</div>
					{/if}

					{#if behaviorTrace.history}
						<div class="text-xs font-mono tabular-nums text-fg-muted">
							history: {behaviorTrace.history.messages_sent}/{behaviorTrace.history.messages_total} sent{behaviorTrace
								.history.truncated
								? ' (truncated)'
								: ''}
						</div>
					{/if}

					{#if behaviorTrace.tools_used.length > 0}
						<div>
							<p class="text-xs font-mono uppercase tracking-[0.05em] text-fg-subtle mb-1">
								Tools used
							</p>
							<div class="flex flex-wrap gap-1">
								{#each behaviorTrace.tools_used as toolName (toolName)}
									<Badge variant="neutral" size="sm" class="font-mono">{toolName}</Badge>
								{/each}
							</div>
						</div>
					{/if}

					{#if behaviorTrace.resources.length > 0}
						<div>
							<p class="text-xs font-mono uppercase tracking-[0.05em] text-fg-subtle mb-1">
								Resources
							</p>
							<div class="flex flex-wrap gap-1">
								{#each behaviorTrace.resources as resource, i (i)}
									<Badge variant="neutral" size="sm" class="font-mono">{resource.type}:{resource.uri}</Badge>
								{/each}
							</div>
						</div>
					{/if}

					{#if behaviorTrace.memory}
						<div>
							<p class="text-xs font-mono uppercase tracking-[0.05em] text-fg-subtle mb-1">Memory</p>
							<pre
								class="text-xs font-mono whitespace-pre-wrap overflow-x-auto overflow-y-auto max-h-32 bg-surface-1 border border-line rounded p-2 text-fg-muted">{pretty(behaviorTrace.memory)}</pre>
						</div>
					{/if}
				</div>
			{/if}
		</div>
	{/if}
{/snippet}

{#snippet traceCard(trace: AdminChatCallTrace)}
	{@const isOpen = !!expandedTraces[trace.id]}
	<div class="border border-line-strong rounded bg-surface-2 overflow-hidden">
		<button
			type="button"
			class="w-full flex items-center gap-2 px-2.5 py-1.5 text-left hover:bg-surface-3 transition-colors"
			onclick={() => toggleTrace(trace.id)}
		>
			<Icon
				name={isOpen ? 'chevron-down' : 'chevron-right'}
				className="w-3.5 h-3.5 text-fg-subtle flex-shrink-0"
			/>
			<span class="text-xs font-mono tabular-nums text-fg-muted truncate">
				call {trace.iteration} · {trace.provider}/{trace.model} · {trace.purpose} · {trace.duration_ms}ms
				· {trace.prompt_tokens ?? '?'}→{trace.completion_tokens ?? '?'} tok
			</span>
		</button>

		{#if isOpen}
			<div class="px-2.5 pb-2.5 space-y-3 border-t border-line">
				{#if trace.request_system}
					<div>
						<p class="text-xs font-mono uppercase tracking-[0.05em] text-fg-subtle mt-2 mb-1">
							System prompt
						</p>
						<pre
							class="text-xs font-mono whitespace-pre-wrap overflow-y-auto max-h-48 bg-surface-1 border border-line rounded p-2 text-fg-muted">{trace.request_system}</pre>
					</div>
				{/if}

				<div>
					<p class="text-xs font-mono uppercase tracking-[0.05em] text-fg-subtle mb-1">
						Request messages
					</p>
					<div class="overflow-y-auto max-h-64 space-y-1.5">
						{#each trace.request_messages as m, i (i)}
							{#if m.role === 'system' && typeof m.content === 'string' && m.content === trace.request_system}
								<pre
									class="text-xs font-mono whitespace-pre-wrap overflow-x-auto bg-surface-1 border border-line rounded p-2 text-fg-subtle italic"><span class="text-fg font-medium not-italic">{m.role}:</span> same text as the System prompt panel above (this is its actual position in the request — it is sent once)</pre>
							{:else}
								<pre
									class="text-xs font-mono whitespace-pre-wrap overflow-x-auto bg-surface-1 border border-line rounded p-2 text-fg-muted"><span class="text-fg font-medium">{m.role}:</span> {typeof m.content === 'string' ? m.content : pretty(m.content)}</pre>
							{/if}
						{/each}
					</div>
				</div>

				<div>
					<p class="text-xs font-mono uppercase tracking-[0.05em] text-fg-subtle mb-1">
						Request params
					</p>
					<pre
						class="text-xs font-mono whitespace-pre-wrap overflow-x-auto overflow-y-auto max-h-32 bg-surface-1 border border-line rounded p-2 text-fg-muted">{pretty(trace.request_params)}</pre>
				</div>

				{#if trace.request_tools && trace.request_tools.length > 0}
					<div>
						<p class="text-xs font-mono uppercase tracking-[0.05em] text-fg-subtle mb-1">
							Tools offered
						</p>
						<div class="flex flex-wrap gap-1">
							{#each trace.request_tools as toolName (toolName)}
								<Badge variant="neutral" size="sm" class="font-mono">{toolName}</Badge>
							{/each}
						</div>
					</div>
				{/if}

				{#if trace.response_text}
					<div>
						<p class="text-xs font-mono uppercase tracking-[0.05em] text-fg-subtle mb-1">
							Response text
						</p>
						<pre
							class="text-xs font-mono whitespace-pre-wrap overflow-y-auto max-h-48 bg-surface-1 border border-line rounded p-2 text-fg-muted">{trace.response_text}</pre>
					</div>
				{/if}

				{#if trace.response_tool_calls && trace.response_tool_calls.length > 0}
					<div>
						<p class="text-xs font-mono uppercase tracking-[0.05em] text-fg-subtle mb-1">
							Response tool calls
						</p>
						<pre
							class="text-xs font-mono whitespace-pre-wrap overflow-x-auto overflow-y-auto max-h-48 bg-surface-1 border border-line rounded p-2 text-fg-muted">{pretty(trace.response_tool_calls)}</pre>
					</div>
				{/if}
			</div>
		{/if}
	</div>
{/snippet}
