<script lang="ts">
	import type { Snippet } from 'svelte';
	import { logger } from '$lib/utils/logger';
	import BaseModal from './BaseModal.svelte';
	import ConfirmFooter from './ConfirmFooter.svelte';
	import {
		createConfirmSettlementGate,
		getConfirmKeyboardAction,
		settleIfEligible
	} from './confirmKeyboard';

	let {
		isOpen = false,
		title = '',
		subtitle = '',
		subtitleWrap = false,
		splitOnPhone = false,
		size = 'md',
		sizeClass = '',
		compact = false,
		hideCloseButton = false,
		dialogRole = 'dialog',
		labelledBy,
		busy = false,
		closeable,
		keyboard = true,
		resetKey,
		confirmLabel,
		confirmVariant = 'primary',
		confirmDisabled = false,
		cancelLabel,
		hideCancel = false,
		stackOnPhone = false,
		secondaryLabel,
		secondaryIcon,
		secondaryBusy = false,
		secondaryDisabled = false,
		summary,
		leftActions,
		footerContent,
		headerContent,
		headerIconContent,
		onConfirm,
		onCancel,
		onSecondary,
		children
	}: {
		isOpen?: boolean;
		title?: string;
		subtitle?: string;
		subtitleWrap?: boolean;
		splitOnPhone?: boolean;
		size?: 'sm' | 'md' | 'lg' | 'xl' | 'full';
		sizeClass?: string;
		compact?: boolean;
		hideCloseButton?: boolean;
		dialogRole?: 'dialog' | 'alertdialog';
		labelledBy?: string;
		busy?: boolean;
		closeable?: boolean;
		keyboard?: boolean;
		resetKey?: unknown;
		confirmLabel: string;
		confirmVariant?: 'primary' | 'danger';
		confirmDisabled?: boolean;
		cancelLabel?: string;
		hideCancel?: boolean;
		stackOnPhone?: boolean;
		secondaryLabel?: string;
		secondaryIcon?: string;
		secondaryBusy?: boolean;
		secondaryDisabled?: boolean;
		summary?: string;
		leftActions?: Snippet;
		footerContent?: Snippet;
		headerContent?: Snippet;
		headerIconContent?: Snippet;
		onConfirm: () => unknown;
		onCancel: () => void;
		onSecondary?: () => void;
		children?: Snippet;
	} = $props();

	const gate = createConfirmSettlementGate();
	let locked = $derived(busy || secondaryBusy);

	$effect(() => {
		void resetKey;
		if (isOpen) gate.reset();
	});

	function failed(error: unknown) {
		gate.reset();
		logger.error('Confirm action failed:', error);
	}

	function runConfirm() {
		let result: unknown;
		try {
			result = onConfirm();
		} catch (error) {
			failed(error);
			return;
		}
		if (result instanceof Promise) {
			result.then((value) => {
				if (value === false) gate.reset();
			}, failed);
		} else if (result === false) {
			gate.reset();
		}
	}

	export function confirm() {
		return settleIfEligible(gate, isOpen && !locked && !confirmDisabled, runConfirm);
	}

	export function cancel() {
		return settleIfEligible(gate, isOpen && !locked, onCancel);
	}

	export function settle(callback: () => void) {
		return settleIfEligible(gate, isOpen && !locked, callback);
	}

	function handleKeydown(event: KeyboardEvent) {
		if (event.key !== 'Enter' || !isOpen || !keyboard || locked) return;
		const { action, suppress } = getConfirmKeyboardAction(event);
		if (action === 'confirm') confirm();
		if (suppress) event.preventDefault();
	}
</script>

<svelte:window onkeydowncapture={handleKeydown} />

{#snippet footerSlot()}
	{#if footerContent}
		{@render footerContent()}
	{:else}
		<ConfirmFooter
			{cancelLabel}
			{confirmLabel}
			{confirmVariant}
			{busy}
			{confirmDisabled}
			{hideCancel}
			{stackOnPhone}
			{splitOnPhone}
			{secondaryLabel}
			{secondaryIcon}
			{secondaryBusy}
			{secondaryDisabled}
			{summary}
			{leftActions}
			onCancel={cancel}
			onConfirm={confirm}
			{onSecondary}
		/>
	{/if}
{/snippet}

{#if headerContent || headerIconContent}
	<BaseModal
	{isOpen}
	{title}
	{subtitle}
	{subtitleWrap}
	{size}
	{sizeClass}
	{compact}
	{hideCloseButton}
	{dialogRole}
	{labelledBy}
	closeable={closeable ?? !locked}
	handleEscapeKey={keyboard}
	on:close={cancel}
>
		<svelte:fragment slot="header">
			{#if headerContent}{@render headerContent()}{/if}
		</svelte:fragment>
		<svelte:fragment slot="headerIcon">
			{#if headerIconContent}{@render headerIconContent()}{/if}
		</svelte:fragment>
		{@render children?.()}
		<svelte:fragment slot="footer">
			{@render footerSlot()}
		</svelte:fragment>
	</BaseModal>
{:else}
	<BaseModal
	{isOpen}
	{title}
	{subtitle}
	{subtitleWrap}
	{size}
	{sizeClass}
	{compact}
	{hideCloseButton}
	{dialogRole}
	{labelledBy}
	closeable={closeable ?? !locked}
	handleEscapeKey={keyboard}
	on:close={cancel}
>
		{@render children?.()}
		<svelte:fragment slot="footer">
			{@render footerSlot()}
		</svelte:fragment>
	</BaseModal>
{/if}
