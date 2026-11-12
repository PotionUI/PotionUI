<script lang="ts" module>
	export interface RegistrationSubmitValues {
		username: string;
		email: string;
		password: string;
		claimCode?: string;
	}

	export interface RegistrationSubmitResult {
		success: boolean;
		error?: string;
		fieldErrors?: Record<string, string>;
	}
</script>

<script lang="ts">
	import { Alert, Button } from '$lib/components/ui';
	import { validateClaimFields, validateRegisterFields } from './registrationValidation';

	let {
		mode,
		showClaimCode = false,
		claimCodeRequired = false,
		passwordPlaceholder = mode === 'claim' ? 'At least 8 characters' : 'Create a password',
		confirmPasswordLabel = mode === 'claim' ? 'Confirm password' : 'Confirm Password',
		submitLabel,
		submitLoadingLabel,
		embedded = false,
		onSubmit
	}: {
		mode: 'register' | 'claim';
		showClaimCode?: boolean;
		claimCodeRequired?: boolean;
		passwordPlaceholder?: string;
		confirmPasswordLabel?: string;
		submitLabel: string;
		submitLoadingLabel: string;
		embedded?: boolean;
		onSubmit: (values: RegistrationSubmitValues) => Promise<RegistrationSubmitResult>;
	} = $props();

	const SHOWN_FIELDS = ['username', 'email', 'password'];

	let username = $state('');
	let email = $state('');
	let password = $state('');
	let confirmPassword = $state('');
	let claimCode = $state('');
	let isSubmitting = $state(false);
	let error = $state('');
	let fieldErrors = $state<Record<string, string>>({});

	async function handleSubmit(event: Event) {
		event.preventDefault();
		error = '';
		fieldErrors = {};

		const validationError =
			mode === 'claim'
				? validateClaimFields({ username, email, password, confirmPassword, claimCode, claimCodeRequired })
				: validateRegisterFields({ username, email, password, confirmPassword });

		if (validationError) {
			error = validationError;
			return;
		}

		isSubmitting = true;
		const result = await onSubmit({
			username,
			email,
			password,
			claimCode: showClaimCode ? claimCode : undefined
		});
		isSubmitting = false;

		if (!result.success) {
			fieldErrors = result.fieldErrors ?? {};
			const shownOnField = SHOWN_FIELDS.some((field) => fieldErrors[field]);
			error = shownOnField ? '' : result.error || 'Something went wrong';
		}
	}
</script>

{#if error}
	<Alert variant="danger" class="mt-6">{error}</Alert>
{/if}

<form onsubmit={handleSubmit}>
	<div class="{embedded ? 'mt-0' : 'mt-[38px]'} flex flex-col gap-[22px]">
		<div class="flex flex-col">
			<label for="username" class="font-mono text-2xs uppercase tracking-[0.1em] text-fg-subtle">
				Username
			</label>
			<input
				id="username"
				type="text"
				bind:value={username}
				disabled={isSubmitting}
				aria-invalid={fieldErrors.username ? 'true' : undefined}
				aria-describedby={fieldErrors.username ? 'username-error' : undefined}
				autocomplete="username"
				class="mt-1.5 h-[30px] w-full border-b border-line-strong bg-transparent text-[15px] text-fg transition-colors focus:border-signal focus:outline-none disabled:cursor-not-allowed disabled:opacity-50"
			/>
			{#if fieldErrors.username}
				<p id="username-error" class="mt-1.5 text-xs text-danger" role="alert">{fieldErrors.username}</p>
			{/if}
		</div>

		<div class="flex flex-col">
			<label for="email" class="font-mono text-2xs uppercase tracking-[0.1em] text-fg-subtle">
				Email
			</label>
			<input
				id="email"
				type="email"
				bind:value={email}
				disabled={isSubmitting}
				aria-invalid={fieldErrors.email ? 'true' : undefined}
				aria-describedby={fieldErrors.email ? 'email-error' : undefined}
				autocomplete="email"
				class="mt-1.5 h-[30px] w-full border-b border-line-strong bg-transparent text-[15px] text-fg transition-colors focus:border-signal focus:outline-none disabled:cursor-not-allowed disabled:opacity-50"
			/>
			{#if fieldErrors.email}
				<p id="email-error" class="mt-1.5 text-xs text-danger" role="alert">{fieldErrors.email}</p>
			{/if}
		</div>

		<div class="flex flex-col">
			<label for="password" class="font-mono text-2xs uppercase tracking-[0.1em] text-fg-subtle">
				Password
			</label>
			<input
				id="password"
				type="password"
				bind:value={password}
				placeholder={passwordPlaceholder}
				disabled={isSubmitting}
				aria-invalid={fieldErrors.password ? 'true' : undefined}
				aria-describedby={fieldErrors.password ? 'password-error' : undefined}
				autocomplete="new-password"
				class="mt-1.5 h-[30px] w-full border-b border-line-strong bg-transparent text-[15px] text-fg transition-colors focus:border-signal focus:outline-none placeholder:text-fg-subtle disabled:cursor-not-allowed disabled:opacity-50"
			/>
			{#if fieldErrors.password}
				<p id="password-error" class="mt-1.5 text-xs text-danger" role="alert">{fieldErrors.password}</p>
			{/if}
		</div>

		<div class="flex flex-col">
			<label
				for="confirmPassword"
				class="font-mono text-2xs uppercase tracking-[0.1em] text-fg-subtle"
			>
				{confirmPasswordLabel}
			</label>
			<input
				id="confirmPassword"
				type="password"
				bind:value={confirmPassword}
				disabled={isSubmitting}
				autocomplete="new-password"
				class="mt-1.5 h-[30px] w-full border-b border-line-strong bg-transparent text-[15px] text-fg transition-colors focus:border-signal focus:outline-none disabled:cursor-not-allowed disabled:opacity-50"
			/>
		</div>

		{#if showClaimCode}
			<div class="flex flex-col">
				<label for="claimCode" class="font-mono text-2xs uppercase tracking-[0.1em] text-fg-subtle">
					Claim code
				</label>
				<input
					id="claimCode"
					type="text"
					bind:value={claimCode}
					disabled={isSubmitting}
					autocomplete="off"
					class="mt-1.5 h-[30px] w-full border-b border-line-strong bg-transparent font-mono text-[15px] text-fg transition-colors focus:border-signal focus:outline-none disabled:cursor-not-allowed disabled:opacity-50"
				/>
				<p class="mt-1.5 text-sm text-fg-subtle" data-claim-code-hint>
					PotionUI printed it when it started. In Docker, run
					<span class="font-mono">docker logs &lt;container&gt;</span>. It is also in
					<span class="font-mono">storage/setup_claim_token</span> on the server.
				</p>
			</div>
		{/if}
	</div>

	<div class="{embedded ? 'mt-6' : 'mt-[34px]'}">
		<Button
			type="submit"
			variant="primary"
			size="lg"
			class="w-full"
			loading={isSubmitting}
			disabled={isSubmitting}
		>
			{isSubmitting ? submitLoadingLabel : submitLabel}
		</Button>
	</div>
</form>
