vec4 effect(vec4 color, vec2 uv) {
    vec3 v = floor(color.rgb * 255.0 + 0.5) / 255.0;
    if (u_amount <= 0.0) return vec4(v, color.a);
    float n = float(u_table_size);
    vec3 graded = texture(u_table, (v * (n - 1.0) + 0.5) / n).rgb;
    vec3 mixed = v + u_amount * (graded - v);
    return vec4(clamp(floor(mixed * 255.0 + 0.0001), 0.0, 255.0) / 255.0, color.a);
}
