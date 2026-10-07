export function roleLabel(role: string): string {
	return role === 'ADMIN' ? 'Admin' : 'User';
}

export function signedInAgo(signedInAt: number, now: number = Date.now()): string {
	const minutes = Math.floor((now - signedInAt) / 60_000);
	if (minutes < 1) return 'signed in just now';
	if (minutes < 60) return `signed in ${minutes} m ago`;
	const hours = Math.floor(minutes / 60);
	if (hours < 24) return `signed in ${hours} h ago`;
	return `signed in ${Math.floor(hours / 24)} d ago`;
}
