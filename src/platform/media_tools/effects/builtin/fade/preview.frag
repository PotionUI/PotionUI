vec3 fadeBy(vec3 v, float progress) {
    float k = floor(clamp(progress, 0.0, 1.0) * 65535.0);
    return floor((v * k + 32768.0) / 65536.0);
}

vec4 effect(vec4 color, vec2 uv) {
    vec3 v = floor(color.rgb * 255.0 + 0.5);
    if (u_fin > 0.0) v = fadeBy(v, u_time / u_fin);
    if (u_fout > 0.0) {
        float k = 65535.0 - floor(clamp((u_time - u_out_start) / u_fout, 0.0, 1.0) * 65535.0);
        v = floor((v * k + 32768.0) / 65536.0);
    }
    return vec4(v / 255.0, color.a);
}
