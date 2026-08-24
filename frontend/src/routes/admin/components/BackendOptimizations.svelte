<script lang="ts">
	import { onDestroy, onMount } from 'svelte';
	import { logger, getApiErrorMessage } from '$lib/utils/logger';
	import { Button, Badge, Spinner } from '$lib/components/ui';
	import BaseModal from '$lib/components/modals/BaseModal.svelte';
	import { api } from '$lib/services/api/index';
	import {
		getBackendOptimizations,
		installBackendOptimization,
		getCurrentOptimizationJob,
		cancelCurrentOptimizationJob,
		setAttentionBackend,
		setEngineFlags,
		restartApp,
		runOptimizationBenchmark
	} from '$lib/services/admin-api';
	import type {
		BackendOptimizations,
		OptimizationStatus,
		AttentionBenchmark
	} from '$lib/services/admin-api';
	import { toasts } from '$lib/stores/toast';
	import { confirmDialog } from '$lib/stores/confirm';

	export let backendId: string;

	let loading = false;
	let error: string | null = null;
	let data: BackendOptimizations | null = null;
	let pinning = false;

	type EngineFlagId = 'torch_compile' | 'stream_prefetch';
	let savingFlag: EngineFlagId | null = null;
	const engineFlagDefs: { id: EngineFlagId; name: string; description: string }[] = [
		{
			id: 'torch_compile',
			name: 'Torch compile',
			description:
				'Regional per-block torch.compile — engages only on fully resident, non-quantized models; first use per model pays a short warmup'
		},
		{
			id: 'stream_prefetch',
			name: 'Stream prefetch',
			description: 'Overlap layer uploads with compute under partial residency'
		}
	];

	// Install modal / job polling
	let modalOpt: OptimizationStatus | null = null;
	let jobStatus: 'running' | 'success' | 'failed' | 'cancelled' | null = null;
	let jobLog: string[] = [];
	let jobOffset = 0;
	let jobResult: { active_backend: string } | null = null;
	let jobError: string | null = null;
	let cancelling = false;
	let pollHandle: ReturnType<typeof setInterval> | null = null;
	let logEl: HTMLPreElement | null = null;

	// Restart
	let restarting = false;

	// Attention benchmark
	let benchmarking = false;
	let benchmarkError: string | null = null;
	let benchmark: AttentionBenchmark | null = null;

	async function load() {
		loading = true;
		error = null;
		try {
			const response = await getBackendOptimizations(backendId);
			if (response.success && response.data) {
				data = response.data;
			} else {
				error = response.message || 'Failed to load optimizations';
			}
		} catch (e: unknown) {
			error = getApiErrorMessage(e, 'Failed to load optimizations');
		} finally {
			loading = false;
		}
	}

	onMount(load);

	function smLabel(cap: [number, number] | null): string {
		if (!cap) return 'unknown';
		return `sm${cap[0]}${cap[1]}`;
	}

	function statusVariant(opt: OptimizationStatus): 'success' | 'signal' | 'neutral' | 'warning' {
		if (opt.active) return 'success';
		if (opt.installed) return 'signal';
		if (opt.installable) return 'neutral';
		return 'warning';
	}

	function statusLabel(opt: OptimizationStatus): string {
		if (opt.active) return 'Active';
		if (opt.installed) return `Installed v${opt.installed_version}`;
		if (opt.installable) return 'Available';
		return 'Unavailable';
	}

	/** The one-click "install a matching nvcc into the venv" catalog entry, if the server offers it. */
	function cudaToolchainOpt(d: BackendOptimizations): OptimizationStatus | undefined {
		return d.optimizations.find((o) => o.opt_id === 'cuda_toolchain');
	}

	async function onPinChange(event: Event) {
		if (!data) return;
		const backend = (event.currentTarget as HTMLSelectElement).value as
			| 'auto'
			| 'sdpa'
			| 'sage'
			| 'sage2'
			| 'flash';
		pinning = true;
		try {
			const response = await setAttentionBackend(backendId, backend);
			if (response.success && response.data) {
				data = {
					...data,
					pinned_backend: response.data.pinned_backend,
					system: { ...data.system, active_backend: response.data.active_backend }
				};
				toasts.success(`Attention backend set to ${response.data.active_backend}`);
			} else {
				toasts.error(response.message || 'Failed to set attention backend');
			}
		} catch (e: unknown) {
			toasts.error(getApiErrorMessage(e, 'Failed to set attention backend'));
		} finally {
			pinning = false;
		}
	}

	async function onFlagChange(flag: EngineFlagId, event: Event) {
		if (!data) return;
		const checked = (event.currentTarget as HTMLInputElement).checked;
		savingFlag = flag;
		try {
			const response = await setEngineFlags(backendId, { [flag]: checked ? 'on' : 'off' });
			if (response.success && response.data) {
				data = { ...data, engine_flags: response.data.engine_flags };
			} else {
				toasts.error(response.message || 'Failed to update engine flags');
				data = { ...data };
			}
		} catch (e: unknown) {
			toasts.error(getApiErrorMessage(e, 'Failed to update engine flags'));
			data = { ...data };
		} finally {
			savingFlag = null;
		}
	}

	async function runBenchmark() {
		benchmarking = true;
		benchmarkError = null;
		try {
			const response = await runOptimizationBenchmark(backendId);
			if (response.success && response.data) {
				benchmark = response.data;
			} else {
				benchmarkError = response.message || 'Benchmark failed to run';
			}
		} catch (e: unknown) {
			benchmarkError = getApiErrorMessage(e, 'Benchmark failed to run');
		} finally {
			benchmarking = false;
		}
	}

	function openInstallModal(opt: OptimizationStatus) {
		modalOpt = opt;
		jobStatus = null;
		jobLog = [];
		jobOffset = 0;
		jobResult = null;
		jobError = null;
		startInstall(opt);
	}

	async function startInstall(opt: OptimizationStatus) {
		try {
			const response = await installBackendOptimization(backendId, opt.opt_id);
			if (!response.success) {
				toasts.error(response.message || `Failed to start install for "${opt.name}"`);
				modalOpt = null;
				return;
			}
			jobStatus = 'running';
			startPolling();
		} catch (e: unknown) {
			toasts.error(getApiErrorMessage(e, `Failed to start install for "${opt.name}"`));
			modalOpt = null;
		}
	}

	function startPolling() {
		stopPolling();
		pollHandle = setInterval(pollJob, 1000);
		pollJob();
	}

	function stopPolling() {
		if (pollHandle !== null) {
			clearInterval(pollHandle);
			pollHandle = null;
		}
	}

	async function pollJob() {
		if (!modalOpt) return;
		try {
			const response = await getCurrentOptimizationJob(backendId, jobOffset);
			if (!response.success || !response.data) return;
			const job = response.data;
			if (!job.active && !job.status) {
				return;
			}
			if (job.log && job.log.length > 0) {
				jobLog = [...jobLog, ...job.log];
				requestAnimationFrame(() => {
					if (logEl) logEl.scrollTop = logEl.scrollHeight;
				});
			}
			if (typeof job.next_offset === 'number') {
				jobOffset = job.next_offset;
			}
			jobStatus = job.status ?? jobStatus;
			if (job.status && job.status !== 'running') {
				stopPolling();
				jobResult = job.result ?? null;
				jobError = job.error ?? null;
				if (job.status === 'success') {
					await load();
				}
			}
		} catch (e) {
			logger.error('Failed to poll optimization job:', e);
		}
	}

	async function cancelInstall() {
		cancelling = true;
		try {
			await cancelCurrentOptimizationJob(backendId);
		} catch (e: unknown) {
			toasts.error(getApiErrorMessage(e, 'Failed to cancel install'));
		} finally {
			cancelling = false;
		}
	}

	function retryInstall() {
		if (modalOpt) openInstallModal(modalOpt);
	}

	function closeInstallModal() {
		stopPolling();
		modalOpt = null;
	}

	async function confirmRestart() {
		if (
			!(await confirmDialog({
				title: 'Restart the app now?',
				message: 'Active generations will be interrupted.',
				variant: 'danger'
			}))
		)
			return;
		restarting = true;
		try {
			await restartApp();
		} catch (e) {
			// The connection typically drops mid-response as the process exec's — expected.
		}
		await waitForServerBack();
		restarting = false;
		await load();
	}

	async function waitForServerBack() {
		// Give the process a moment to actually exit before polling for it to come back.
		await new Promise((resolve) => setTimeout(resolve, 1500));
		for (let attempt = 0; attempt < 120; attempt++) {
			try {
				const response = await api.getClient().get('/health');
				if (response.status === 200) return;
			} catch {
				// not back yet
			}
			await new Promise((resolve) => setTimeout(resolve, 1000));
		}
	}

	onDestroy(() => {
		stopPolling();
	});
</script>

<div>
	{#if data}
		<div class="flex items-center gap-2 mb-3">
			<span class="text-xs font-mono uppercase tracking-[0.07em] text-fg-subtle">Active attention backend</span>
			<Badge variant="signal" size="sm" class="font-mono">
				{data.system.active_backend}
			</Badge>
		</div>
	{/if}

	<div class="space-y-3">
			{#if loading}
				<div class="flex items-center gap-2 py-3">
					<Spinner size="sm" />
					<span class="text-xs text-fg-muted">Probing system…</span>
				</div>
			{:else if error}
				<div class="rounded border border-danger/25 bg-danger/10 text-danger px-3 py-2 text-xs">
					{error}
				</div>
			{:else if data}
				<!-- System report -->
				<div class="rounded border border-line bg-surface-1 px-3 py-2">
					<div class="grid grid-cols-2 gap-x-4 gap-y-1 text-2xs font-mono tabular-nums text-fg-muted">
						<span
							>GPU: <span class="text-fg"
								>{data.system.gpu_name ?? 'none'} ({smLabel(data.system.compute_capability)})</span
							></span
						>
						<span
							>nvcc: <span class="text-fg"
								>{data.system.nvcc_found
									? data.system.nvcc_version
										? `${data.system.nvcc_version[0]}.${data.system.nvcc_version[1]}`
										: 'found'
									: 'not found'}{data.system.nvcc_source === 'venv' ? ' (venv)' : ''}</span
							></span
						>
						<span
							>torch: <span class="text-fg">{data.system.torch_version}</span></span
						>
						<span
							>cuda: <span class="text-fg">{data.system.torch_cuda_version ?? 'unknown'}</span></span
						>
					</div>
					{#if !data.system.nvcc_found}
						{@const toolchain = cudaToolchainOpt(data)}
						<div class="flex items-center gap-2 mt-1.5">
							<p class="text-2xs text-warning leading-relaxed flex-1">
								Install the CUDA toolkit matching your torch build (cuda {data.system
									.torch_cuda_version ?? '?'}) to compile optimizations from source.
							</p>
							{#if toolchain && toolchain.installable}
								<Button variant="secondary" size="xs" onclick={() => openInstallModal(toolchain)}>
									Align
								</Button>
							{/if}
						</div>
					{:else if !data.system.nvcc_cuda_matches_torch}
						{@const toolchain = cudaToolchainOpt(data)}
						<div class="flex items-center gap-2 mt-1.5">
							<p class="text-2xs text-warning leading-relaxed flex-1">
								nvcc CUDA version doesn't match torch's CUDA build — compiled extensions may fail
								to load.
							</p>
							{#if toolchain && toolchain.installable}
								<Button variant="secondary" size="xs" onclick={() => openInstallModal(toolchain)}>
									Align
								</Button>
							{/if}
						</div>
					{/if}
				</div>

				<!-- Attention backend pin -->
				<div class="flex items-center gap-2">
					<label for="attn-pin-{backendId}" class="text-xs text-fg-muted whitespace-nowrap">
						Attention backend
					</label>
					<select
						id="attn-pin-{backendId}"
						class="input text-xs font-mono py-1"
						value={data.pinned_backend ?? 'auto'}
						on:change={onPinChange}
						disabled={pinning}
					>
						<option value="auto">auto</option>
						{#each data.system.available_backends as backend (backend)}
							<option value={backend}>{backend}</option>
						{/each}
					</select>
					{#if pinning}<Spinner size="sm" />{/if}
					<span class="text-2xs font-mono tabular-nums text-fg-subtle ml-auto"
						>active: {data.system.active_backend}</span
					>
					<Button
						variant="secondary"
						size="xs"
						loading={benchmarking}
						disabled={modalOpt !== null && jobStatus === 'running'}
						onclick={runBenchmark}
					>
						{benchmarking ? 'Benchmarking…' : 'Benchmark'}
					</Button>
				</div>

				{#if benchmarkError}
					<div class="rounded border border-warning/25 bg-warning/10 text-warning px-3 py-2 text-2xs">
						{benchmarkError}
					</div>
				{:else if benchmark}
					<div class="rounded border border-line bg-surface-1 px-3 py-2 overflow-x-auto">
						<table class="w-full text-2xs font-mono tabular-nums">
							<thead>
								<tr class="text-fg-subtle text-left">
									<th class="font-normal pb-1 pr-3">backend</th>
									<th class="font-normal pb-1 pr-3">ms</th>
									<th class="font-normal pb-1">speedup</th>
								</tr>
							</thead>
							<tbody>
								{#each benchmark.results as row (row.backend)}
									<tr
										class={row.backend === benchmark.active_backend
											? 'text-signal'
											: row.ok
												? 'text-fg'
												: 'text-danger'}
									>
										<td class="pr-3 py-0.5">{row.backend}</td>
										<td class="pr-3 py-0.5">{row.ok ? row.ms?.toFixed(2) : '—'}</td>
										<td class="py-0.5">
											{#if !row.ok}
												<span title={row.error ?? undefined}
													>{(row.error ?? 'error').slice(0, 40)}</span
												>
											{:else if row.speedup != null}
												{row.speedup.toFixed(2)}x
											{:else}
												—
											{/if}
										</td>
									</tr>
								{/each}
							</tbody>
						</table>
						<p class="text-2xs text-fg-subtle mt-1.5">
							attention only, shape {benchmark.shape.join('x')} {benchmark.dtype}, {benchmark.iterations}
							iters — not end-to-end step time
						</p>
					</div>
				{/if}

				<!-- Engine flags -->
				<div class="space-y-1.5">
					<span class="text-xs font-mono uppercase tracking-[0.07em] text-fg-subtle">Engine flags</span>
					<div class="rounded border border-line bg-surface-1 divide-y divide-line">
						{#each engineFlagDefs as flag (flag.id)}
							<div class="flex items-center justify-between gap-4 px-3 py-2.5">
								<div class="flex-1 min-w-0">
									<label for="engine-flag-{flag.id}-{backendId}" class="text-sm font-medium text-fg">
										{flag.name}
									</label>
									<p class="text-xs text-fg-muted mt-0.5 leading-relaxed">{flag.description}</p>
								</div>
								{#if savingFlag === flag.id}<Spinner size="sm" />{/if}
								<input
									type="checkbox"
									id="engine-flag-{flag.id}-{backendId}"
									class="w-4 h-4 text-signal border-line-strong rounded focus:ring-signal flex-shrink-0"
									checked={data.engine_flags[flag.id]}
									disabled={savingFlag !== null}
									on:change={(e) => onFlagChange(flag.id, e)}
								/>
							</div>
						{/each}
					</div>
				</div>

				<!-- Optimizations list -->
				<div class="space-y-2">
					{#each data.optimizations as opt (opt.opt_id)}
						<div class="rounded border border-line bg-surface-1 px-3 py-2.5">
							<div class="flex items-start justify-between gap-2">
								<div class="flex-1 min-w-0">
									<div class="flex items-center gap-2 flex-wrap">
										<span class="text-sm font-medium text-fg">{opt.name}</span>
										<Badge variant={statusVariant(opt)} size="sm">{statusLabel(opt)}</Badge>
										{#if opt.needs_restart}
											<Badge variant="warning" size="sm">Restart required</Badge>
										{/if}
									</div>
									<p class="text-xs text-fg-muted mt-1 leading-relaxed">{opt.description}</p>
									<p class="text-2xs text-fg-subtle mt-0.5">{opt.benefit}</p>
									{#if !opt.installable}
										{@const unmet = opt.requirements.filter((r) => !r.met)}
										{#if unmet.length > 0}
											<ul class="mt-1.5 space-y-0.5">
												{#each unmet as req (req.id)}
													<li class="text-2xs text-warning leading-relaxed">
														{req.label}{req.detail ? ` — ${req.detail}` : ''}
													</li>
												{/each}
											</ul>
										{/if}
									{/if}
								</div>
								<Button
									variant="secondary"
									size="sm"
									disabled={!opt.installable}
									onclick={() => openInstallModal(opt)}
								>
									Install
								</Button>
							</div>
						</div>
					{/each}
				</div>

				<!-- Restart -->
				<div class="flex items-center justify-between pt-1">
					<span class="text-2xs text-fg-subtle">Some changes need an app restart to take effect.</span>
					<Button variant="secondary" size="sm" loading={restarting} onclick={confirmRestart}>
						{restarting ? 'Restarting…' : 'Restart app'}
					</Button>
				</div>
			{/if}
	</div>
</div>

<!-- Install Modal -->
<BaseModal
	isOpen={!!modalOpt}
	title={modalOpt ? `Installing ${modalOpt.name}` : ''}
	sizeClass="md:max-w-2xl md:w-full"
	on:close={closeInstallModal}
>
	<div class="px-6 py-4 h-full flex flex-col gap-3">
		{#if jobStatus === 'success'}
			<div class="rounded border border-success/25 bg-success/10 text-success px-3 py-2 text-sm">
				Now using: <span class="font-mono">{jobResult?.active_backend}</span>
			</div>
		{:else if jobStatus === 'failed' || jobStatus === 'cancelled'}
			<div class="rounded border border-danger/25 bg-danger/10 text-danger px-3 py-2 text-sm">
				{jobStatus === 'cancelled' ? 'Install cancelled.' : jobError || 'Install failed.'}
			</div>
		{:else}
			<div class="flex items-center gap-2 text-xs text-fg-muted">
				<Spinner size="sm" />
				<span>Compiling / installing… this can take several minutes.</span>
			</div>
		{/if}

		<pre
			bind:this={logEl}
			class="flex-1 min-h-[16rem] overflow-y-auto rounded bg-canvas border border-line px-3 py-2 text-2xs font-mono text-fg-muted whitespace-pre-wrap"
		>{jobLog.join('\n')}</pre>
	</div>

	<svelte:fragment slot="footer">
		<div class="px-6 py-4 flex gap-3">
			{#if jobStatus === 'running'}
				<Button variant="danger" loading={cancelling} onclick={cancelInstall}>Cancel</Button>
			{:else if jobStatus === 'failed' || jobStatus === 'cancelled'}
				<Button variant="secondary" onclick={retryInstall}>Retry</Button>
			{/if}
			<Button variant="secondary" class="flex-1" onclick={closeInstallModal}>
				{jobStatus === 'running' ? 'Hide' : 'Close'}
			</Button>
		</div>
	</svelte:fragment>
</BaseModal>
