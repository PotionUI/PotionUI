/**
 * One-time, styled console banner printed at app boot. Replaces the ad-hoc
 * debug logging that used to spam the console during startup.
 */
export function printStartupBanner(): void {
	console.log(
		'%cPotionUI',
		'font-size:15px;font-weight:600;color:#4D9FFF;padding:2px 0;'
	);
	console.log(
		'%cSelf-hosted AI image & video generation.',
		'color:#8a8f98;font-size:11px;'
	);
}
