## Secret to connect with AWS is manually created using the template below: 

```yaml
apiVersion: v1
kind: Secret
metadata:
  name: aws-credentials
  namespace: external-secrets
type: Opaque
stringData:
  access-key: ""
  secret-key: ""
```
