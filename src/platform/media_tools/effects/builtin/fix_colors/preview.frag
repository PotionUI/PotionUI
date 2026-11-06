vec4 effect(vec4 color, vec2 uv) {
    vec3 mixed = clamp(floor(floor(color.rgb * 255.0 + 0.5) * vec3(u_r, u_g, u_b) + 0.5), 0.0, 255.0);
    float lo = floor(u_lo * 255.0 + 0.5);
    float hi = floor(u_hi * 255.0 + 0.5);
    vec3 levelled = hi > lo ? floor((mixed - lo) * (255.0 / (hi - lo))) : mixed;
    return vec4(clamp(levelled, 0.0, 255.0) / 255.0, color.a);
}
