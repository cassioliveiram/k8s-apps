# k8s-apps
Personal applications in my raspberry k3s Cluster

## ArgoCD

After install: 

- kubectl port-forward svc/argocd-server -n argocd 8080:443
- argocd admin initial-password -n argocd
- argocd login <ARGOCD_SERVER>
- argocd account update-password
- kubectl config get-contexts -o name
- argocd cluster add raspberry

## Pending - Create An Application From A Git Repository
https://github.com/argoproj/argocd-example-apps

 - Certmanager
 - Certmanager Configs
 - Istio Base
 - Istio
 - Istio Configs
 - Istio Gateway
 - Unifi-Controller
