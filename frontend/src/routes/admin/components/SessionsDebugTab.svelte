<script lang="ts">
	import { logger } from '$lib/utils/logger';
	import * as adminApi from '$lib/services/admin-api';
	import type {
		AdminChatSessionSummary,
		AdminChatSessionDetailResult,
		AdminChatCallTrace,
		AdminChatMessage,
		AdminChatBehaviorTrace
	} from '$lib/services/admin-api';
	import { toasts } from '$lib/stores/toast';
	import { confirmDialog } from '$lib/stores/confirm';
	import { timeAgo } from '$lib/utils/relativeTime';
	import Icon from '$lib/components/Icon.svelte';
	import Tooltip from '$lib/components/Tooltip.svelte';
	import { Badge, EmptyState, Spinner, IconButton } from '$lib/components/ui';
	import { DetailHeader, DetailBody, DetailLayout, DetailSection, KVGrid, KVItem } from '$lib/components/detail';
	import { sectionBoxClass } from '$lib/components/detail/detailSection';
	import { DataTable, TablePager, pageCount, clampPage } from '$lib/components/table';
	import { selectPage, clearAll } from '$lib/components/table/selection';
	import SelectionActionBar from '$lib/components/collections/SelectionActionBar.svelte';
	import { DEFAULT_SESSIONS_FILTERS, type SessionsFilters } from './sessionsFilters';
	import { summarizeBulkOutcome, bulkOutcomeMessage } from './llm/bulkResult';

	const PAGE_SIZE_OPTIONS = [20, 50, 100] as const;

	let {
		filters = DEFAULT_SESSIONS_FILTERS,
		total = $bindable(0),
		detailOpen = $bindable(false),
		clearingScope = $bindable<'session' | 'all' | 'sessions' | null>(null)
	}: {
		filters?: SessionsFilters;
		total?: number;
		detailOpen?: boolean;
		clearingScope?: 'session' | 'all' | 'sessions' | null;
	} = $props();

	let sessions = $state<AdminChatSessionSummary[]>([]);
	let offset = $state(0);
	let pageSize = $state<number>(PAGE_SIZE_OPTIONS[0]);
	let listLoading = $state(true);
	let listError = $state<string | null>(null);
	let tracingEnabled = $state(true);
	let lastFiltersKey = $state('');
	let selected = $state<Set<string>>(new Set());

	let selectedSessionId = $state<string | null>(null);
	let detail = $state<AdminChatSessionDetailResult | null>(null);
	let detailLoading = $state(false);
	let detailError = $state<string | null>(null);

	let expandedTraces = $state<Record<string, boolean>>({});
	let expandedSections = $state<Record<string, boolean>>({});

	const page = $derived(Math.floor(offset / pageSize) + 1);
	const pageCountValue = $derived(pageCount(total, pageSize));
	const unattributedTraces = $derived(detail ? detail.traces.filter((t) => t.message_id === null) : []);
	const sessionHasNoTraces = $derived(detail ? detail.traces.length === 0 : false);

	$effect(() => {
		detailOpen = !!selectedSessionId;
	});

	$effect(() => {
		const key = JSON.stringify(filters);
		if (key !== lastFiltersKey) {
			lastFiltersKey = key;
			offset = 0;
			loadSessions();
		}
	});

	export async function clearAllTraces() {
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

	export async function clearAllSessions() {
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

	async function loadSessions() {
		listLoading = true;
		listError = null;
		try {
			const response = await adminApi.getAdminChatSessions(filters.q, pageSize, offset);
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

	function onPageChange(next: number) {
		offset = (clampPage(next, pageCountValue) - 1) * pageSize;
		loadSessions();
	}

	function onPageSizeChange(next: number) {
		pageSize = next;
		offset = 0;
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

	function backToList() {
		selectedSessionId = null;
		detail = null;
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

	async function bulkClearTraces() {
		const ids = [...selected];
		if (!ids.length) return;
		if (!(await confirmDialog({
			title: `Clear traces for ${ids.length} session${ids.length === 1 ? '' : 's'}?`,
			message: 'This cannot be undone.',
			variant: 'danger'
		}))) return;
		const results = await Promise.allSettled(ids.map((id) => adminApi.clearChatCallTraces(id)));
		const outcome = summarizeBulkOutcome(results);
		const { ok, text } = bulkOutcomeMessage(outcome, 'cleared', 'session');
		if (text) (ok ? toasts.success : toasts.error)(text);
		selected = new Set();
		if (selectedSessionId && ids.includes(selectedSessionId)) await selectSession(selectedSessionId);
	}

	async function bulkDeleteSessions() {
		const ids = [...selected];
		if (!ids.length) return;
		if (!(await confirmDialog({
			title: `Delete ${ids.length} session${ids.length === 1 ? '' : 's'}?`,
			message: 'Every message in the selected sessions will be deleted. This cannot be undone.',
			variant: 'danger'
		}))) return;
		let succeeded = 0;
		try {
			const response = await adminApi.adminBulkDeleteChatSessions(ids);
			if (response.success && response.data) succeeded = response.data.deleted_count;
		} catch (e) {
			logger.error('Failed to bulk delete chat sessions:', e);
		}
		const outcome = { total: ids.length, succeeded, failed: ids.length - succeeded };
		const { ok, text } = bulkOutcomeMessage(outcome, 'deleted', 'session');
		if (text) (ok ? toasts.success : toasts.error)(text);
		if (selectedSessionId && ids.includes(selectedSessionId)) backToList();
		selected = new Set();
		await loadSessions();
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

{#snippet userCell(row: AdminChatSessionSummary)}
	<Tooltip text={row.email}><span class="truncate">{row.username}</span></Tooltip>
{/snippet}

{#snippet modeCell(row: AdminChatSessionSummary)}
	<Badge variant="neutral" size="sm" class="font-mono uppercase">{row.mode}</Badge>
{/snippet}

<div class="h-full min-h-0 flex flex-col">
	{#if !listLoading && !tracingEnabled}
		<div
			class="flex-shrink-0 flex items-center gap-2 border-b border-line bg-warning/10 px-4 py-2 text-sm text-warning"
		>
			<Icon name="warning" className="w-4 h-4 flex-shrink-0" />
			<span>
				LLM call tracing is disabled — new chat turns won't be recorded. Enable the
				<span class="font-mono text-xs">chat_llm_call_tracing</span> setting in System Settings to
				capture wire-level traces going forward.
			</span>
		</div>
	{/if}

	{#if selectedSessionId}
		{#if detailLoading}
			<div class="flex-1 flex items-center justify-center">
				<Spinner size="lg" />
			</div>
		{:else if detailError}
			<div class="flex-1 p-5 flex items-center justify-center">
				<EmptyState title="Could not load session" description={detailError} icon="warning" compact />
			</div>
		{:else if detail}
			{@const sessionDetail = detail}
			<DetailHeader title={sessionDetail.session.name || 'Untitled'} icon="chat" backLabel="Sessions" onBack={backToList}>
				{#snippet subtitle()}{sessionDetail.session.username} · {timeAgo(sessionDetail.session.updated_at)}{/snippet}
				{#snippet chips()}
					<Badge variant="neutral" size="sm" class="font-mono uppercase">{sessionDetail.session.mode}</Badge>
				{/snippet}
				{#snippet actions()}
					<Tooltip text="Clear this session's traces">
						<IconButton
							icon="trash"
							label="Clear this session's traces"
							class="text-danger hover:text-danger hover:bg-danger/10"
							disabled={clearingScope !== null}
							onclick={clearSessionTraces}
						/>
					</Tooltip>
				{/snippet}
			</DetailHeader>

			<DetailBody>
				<DetailLayout>
					{#snippet main()}
						{#if sessionHasNoTraces}
							<EmptyState
								icon="chat"
								title="No wire-level call traces"
								description="Traces are recorded only for turns sent while call tracing is enabled."
								compact
							/>
						{/if}

						{#each sessionDetail.session.messages as message (message.id)}
							{@const traces = tracesForMessage(message.id)}
							{@const metadata = message.metadata}
							<div id="message-{message.id}" class={sectionBoxClass(false)}>
								<div class="px-3 py-2 border-b border-line flex items-center justify-between">
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
							<DetailSection label="Unattributed calls">
								<div class="space-y-2">
									{#each unattributedTraces as trace (trace.id)}
										{@render traceCard(trace)}
									{/each}
								</div>
							</DetailSection>
						{/if}
					{/snippet}

					{#snippet aside()}
						<DetailSection label="Contents">
							{#if sessionDetail.session.messages.length === 0}
								<EmptyState icon="chat" title="No messages" description="This session has no messages yet." compact />
							{:else}
								<nav class="space-y-1">
									{#each sessionDetail.session.messages as message, i (message.id)}
										{@const messageTraces = tracesForMessage(message.id)}
										<a
											href="#message-{message.id}"
											class="block truncate text-xs text-fg-muted hover:text-fg"
										>
											{i + 1}. {message.role}{messageTraces.length ? ` (${messageTraces.length} call${messageTraces.length === 1 ? '' : 's'})` : ''}
										</a>
									{/each}
								</nav>
							{/if}
						</DetailSection>
						<DetailSection label="Session">
							<KVGrid>
								<KVItem label="ID" mono full>{sessionDetail.session.id}</KVItem>
								<KVItem label="Status">{sessionDetail.session.status}</KVItem>
								{#if sessionDetail.session.llm_config_id}
									<KVItem label="LLM config" mono full>{sessionDetail.session.llm_config_id}</KVItem>
								{/if}
								<KVItem label="Created" mono>{sessionDetail.session.created_at}</KVItem>
							</KVGrid>
						</DetailSection>
					{/snippet}
				</DetailLayout>
			</DetailBody>
		{/if}
	{:else}
		<div class="flex flex-col gap-3 p-4">
			<SelectionActionBar
				active={selected.size > 0}
				selectedCount={selected.size}
				totalCount={sessions.length}
				onSelectAll={() => (selected = selectPage(selected, sessions.map((s) => s.id)))}
				onClearSelection={() => (selected = clearAll())}
				onClose={() => (selected = clearAll())}
			>
				<svelte:fragment slot="actionsBeforeCollection">
					<button
						class="px-3 py-1.5 text-sm text-fg-muted hover:text-fg hover:bg-surface-2 rounded transition-colors"
						onclick={bulkClearTraces}
					>
						Clear traces
					</button>
					<button
						class="px-4 py-1.5 bg-danger-solid text-white text-sm rounded hover:bg-danger-solid/90 transition-colors font-medium"
						onclick={bulkDeleteSessions}
					>
						Delete
					</button>
				</svelte:fragment>
			</SelectionActionBar>
			<DataTable
				columns={[
					{ key: 'name', label: 'Name', width: 'minmax(160px,1.6fr)', accessor: (r) => r.name || 'Untitled' },
					{ key: 'user', label: 'User', width: 'minmax(120px,1.2fr)', priority: 1, cell: userCell },
					{ key: 'mode', label: 'Mode', width: '110px', cell: modeCell },
					{ key: 'messages', label: 'Messages', width: '100px', mono: true, priority: 1, accessor: (r) => r.message_count },
					{ key: 'updated', label: 'Updated', width: '120px', mono: true, accessor: (r) => timeAgo(r.updated_at) }
				]}
				rows={sessions}
				getRowId={(r) => r.id}
				loading={listLoading}
				selected={selected}
				onSelectedChange={(next) => (selected = next)}
				onRowClick={(r) => selectSession(r.id)}
				isFiltered={!!filters.q.trim()}
			>
				{#snippet emptyState()}
					{#if listError}
						<EmptyState title="Could not load sessions" description={listError} icon="warning" compact />
					{:else}
						<EmptyState
							icon="chat"
							title="No chat sessions yet"
							description="Chat sessions show up here once someone starts a conversation."
							compact
						/>
					{/if}
				{/snippet}
				{#snippet filteredEmptyState()}
					<EmptyState icon="search" title="No sessions match your search" description="Try a different name, user, or email." compact />
				{/snippet}
			</DataTable>
			<TablePager
				{page}
				pageCount={pageCountValue}
				{pageSize}
				pageSizeOptions={PAGE_SIZE_OPTIONS}
				{onPageChange}
				{onPageSizeChange}
			/>
		</div>
	{/if}
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
