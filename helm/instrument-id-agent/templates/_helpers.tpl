{{- define "instrument-id-agent.fullname" -}}instrument-id-agent{{- end }}
{{- define "instrument-id-agent.labels" -}}
app.kubernetes.io/name: instrument-id-agent
app.kubernetes.io/managed-by: Helm
{{- end }}
{{- define "instrument-id-agent.selectorLabels" -}}
app.kubernetes.io/name: instrument-id-agent
{{- end }}
