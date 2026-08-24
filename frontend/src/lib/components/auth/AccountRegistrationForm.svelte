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
		onSubmit
	}: {
		mode: 'register' | 'claim';
		showClaimCode?: boolean;
		claimCodeRequired?: boolean;
		passwordPlaceholder?: string;
		confirmPasswordLabel?: string;
		submitLabel: string;
		submitLoadingLabel: string;
		onSubmit: (values: RegistrationSubmitValues) => Promise<RegistrationSubmitResult>;
	} = $props();

	let username = $state('');
	let email = $state('');
	let password = $state('');
	let confirmPassword = $state('');
	let claimCode = $state('');
	let isSubmitting = $state(false);
	let error = $state('');

	async function handleSubmit(event: Event) {
		event.preventDefault();
		error = '';

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
			error = result.error || 'Something went wrong';
		}
	}
</script>

{#if error}
	<Alert variant="danger" class="mt-6">{error}</Alert>
{/if}

<form onsubmit={handleSubmit}>
	<div class="mt-[38px] flex flex-col gap-[22px]">
		<div class="flex flex-col">
			<label for="username" class="font-mono text-2xs uppercase tracking-[0.1em] text-fg-subtle">
				Username
			</label>
			<input
				id="username"
				type="text"
				bind:value={username}
				disabled={isSubmitting}
				autocomplete="username"
				class="mt-1.5 h-[30px] w-full border-b border-line-strong bg-transparent text-[15px] text-fg transition-colors focus:border-signal focus:outline-none disabled:cursor-not-allowed disabled:opacity-50"
			/>
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
				autocomplete="email"
				class="mt-1.5 h-[30px] w-full border-b border-line-strong bg-transparent text-[15px] text-fg transition-colors focus:border-signal focus:outline-none disabled:cursor-not-allowed disabled:opacity-50"
			/>
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
				autocomplete="new-password"
				class="mt-1.5 h-[30px] w-full border-b border-line-strong bg-transparent text-[15px] text-fg transition-colors focus:border-signal focus:outline-none placeholder:text-fg-subtle disabled:cursor-not-allowed disabled:opacity-50"
			/>
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
				<p class="mt-1.5 text-2xs text-fg-subtle">
					Shown in the terminal where PotionUI was started.
				</p>
			</div>
		{/if}
	</div>

	<div class="mt-[34px]">
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
