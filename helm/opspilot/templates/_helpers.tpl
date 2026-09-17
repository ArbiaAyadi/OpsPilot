{{/* Fonctions reutilisables du chart.
     Un fichier commencant par _ n'est PAS rendu en manifeste : il ne
     contient que des definitions appelees ailleurs. C'est ce qui evite
     de repeter la meme logique de nommage dans douze fichiers -- et
     surtout d'oublier de la corriger dans l'un d'eux. */}}

{{- define "opspilot.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/* trunc 63 : limite d'un label Kubernetes. Depasser produit une
     erreur a l'installation, pas au lint. */}}
{{- define "opspilot.fullname" -}}
{{- if .Values.fullnameOverride }}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- $name := default .Chart.Name .Values.nameOverride }}
{{- if contains $name .Release.Name }}
{{- .Release.Name | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" }}
{{- end }}
{{- end }}
{{- end }}

{{- define "opspilot.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/* Labels communs a TOUTES les ressources. */}}
{{- define "opspilot.labels" -}}
helm.sh/chart: {{ include "opspilot.chart" . }}
{{ include "opspilot.selectorLabels" . }}
{{- if .Chart.AppVersion }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end }}

{{/* Labels de SELECTION : sous-ensemble STABLE des precedents.
     Le selecteur d'un Deployment est IMMUABLE apres creation. Y inclure
     la version rendrait toute mise a jour impossible. */}}
{{- define "opspilot.selectorLabels" -}}
app.kubernetes.io/name: {{ include "opspilot.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{- define "opspilot.serviceAccountName" -}}
{{- if .Values.serviceAccount.create }}
{{- default (include "opspilot.fullname" .) .Values.serviceAccount.name }}
{{- else }}
{{- default "default" .Values.serviceAccount.name }}
{{- end }}
{{- end }}

{{- define "opspilot.secretName" -}}
{{- if .Values.secrets.existingSecret }}
{{- .Values.secrets.existingSecret }}
{{- else }}
{{- printf "%s-secrets" (include "opspilot.fullname" .) }}
{{- end }}
{{- end }}

{{- define "opspilot.db.fullname" -}}
{{- printf "%s-db" (include "opspilot.fullname" .) }}
{{- end }}