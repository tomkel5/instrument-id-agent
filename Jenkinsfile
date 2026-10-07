pipeline {
  agent any
  triggers { githubPush() }
  options { timestamps(); skipDefaultCheckout(true); disableConcurrentBuilds() }
  environment {
    REGISTRY = '10.0.0.230:5000'
    IMAGE_NAME = 'instrument-id/agent'
    IMAGE_TAG = "${env.BUILD_NUMBER}"
    KUBECONFIG_CREDENTIALS_ID = 'instrument-id-kubeconfig'
    NAMESPACE = 'default'
    RELEASE = 'instrument-id-agent'
  }
  stages {
    stage('Checkout') { steps { checkout scm } }
    stage('Test') { steps { sh 'python3 -m unittest discover -s tests -v' } }
    stage('Build and push') { steps { sh 'docker build -t "$REGISTRY/$IMAGE_NAME:$IMAGE_TAG" -t "$REGISTRY/$IMAGE_NAME:latest" . && docker push "$REGISTRY/$IMAGE_NAME:$IMAGE_TAG" && docker push "$REGISTRY/$IMAGE_NAME:latest"' } }
    stage('Deploy') {
      steps {
        withCredentials([
          file(credentialsId: env.KUBECONFIG_CREDENTIALS_ID, variable: 'KUBECONFIG'),
          file(credentialsId: 'codex-auth-json', variable: 'CODEX_AUTH_FILE'),
          string(credentialsId: 'codex-direct-import-agent-api-key', variable: 'INSTRUMENT_ID_API_KEY')
        ]) {
          sh '''
            set +x
            kubectl create secret generic "$RELEASE-auth" --namespace "$NAMESPACE" --from-file=auth.json="$CODEX_AUTH_FILE" --dry-run=client --output=yaml | kubectl apply -f -
            helm upgrade --install "$RELEASE" helm/instrument-id-agent --namespace "$NAMESPACE" --set-string image.tag="$IMAGE_TAG" --set-string apiKey="$INSTRUMENT_ID_API_KEY" --wait --timeout 3m
            kubectl rollout status deployment/"$RELEASE" --namespace "$NAMESPACE" --timeout=3m
          '''
        }
      }
    }
  }
  post { always { sh 'docker image rm "$REGISTRY/$IMAGE_NAME:$IMAGE_TAG" "$REGISTRY/$IMAGE_NAME:latest" >/dev/null 2>&1 || true'; cleanWs(deleteDirs: true, notFailBuild: true) } }
}
