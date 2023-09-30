# k8s-apps
Personal applications in my k8s Cluster

## ArgoCD

After install: 

1- kubectl port-forward svc/argocd-server -n argocd 8080:443
2- argocd admin initial-password -n argocd
3- argocd login <ARGOCD_SERVER>
4- argocd account update-password
5- kubectl config get-contexts -o name
6- argocd cluster add raspberry

## Pending - Create An Application From A Git Repository
https://github.com/argoproj/argocd-example-apps

1 - Certmanager
2 - Certmanager Configs
3 - Istio Base
4 - Istio
5 - Istio Configs
6 - Istio Gateway
7 - Unifi-Controller
