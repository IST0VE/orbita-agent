"""Optional Kubernetes API discovery, with namespaced read-only operations only."""

import re
from urllib.parse import quote

from agent.integrations.http import AdapterError, HTTPClient
from agent.nt.models import failure


def _name(value: str) -> str:
    if not re.fullmatch(r"[a-z0-9][a-z0-9.-]{0,252}", value):
        raise ValueError("invalid Kubernetes resource name")
    return quote(value, safe="")


class Kubernetes:
    def __init__(self, url: str, token: str = ""):
        self.http = HTTPClient(url, token)

    def _get(self, path, **params):
        try:
            result = self.http.json("GET", path, params={"limit": 500, **params})
            if result.get("metadata", {}).get("continue"):
                return failure("KUBERNETES_LIMIT", "more than 500 resources; narrow scope")
            return {"success": True, "data": result, "historical": False}
        except (AdapterError, ValueError, TypeError, AttributeError):
            return failure("KUBERNETES_UNAVAILABLE", "Kubernetes read failed")

    def get_deployments(self, namespace):
        result = self._get(f"/apis/apps/v1/namespaces/{_name(namespace)}/deployments")
        if result["success"]:
            result["data"] = [{"name": r["metadata"]["name"],
                "service": r["metadata"].get("labels", {}).get("app.kubernetes.io/name")
                           or r["metadata"]["name"],
                "replicas": r.get("status", {}).get("replicas", 0),
                "available": r.get("status", {}).get("availableReplicas", 0)}
                for r in result["data"].get("items", [])]
        return result

    def get_pods(self, namespace, service):
        result = self._get(f"/api/v1/namespaces/{_name(namespace)}/pods",
                           labelSelector=f"app.kubernetes.io/name={_name(service)}")
        if result["success"]:
            pods = []
            for pod in result["data"].get("items", []):
                statuses = pod.get("status", {}).get("containerStatuses", [])
                pods.append({"name": pod["metadata"]["name"],
                    "phase": pod.get("status", {}).get("phase", "Unknown"),
                    "restarts": sum(c.get("restartCount", 0) for c in statuses),
                    "reasons": [s.get("reason") for c in statuses
                                for s in c.get("state", {}).values() if s.get("reason")]})
            result["data"] = {"service": service, "pods_total": len(pods),
                "pods_running": sum(p["phase"] == "Running" for p in pods),
                "pods_failed": sum(p["phase"] == "Failed" for p in pods),
                "restarts": sum(p["restarts"] for p in pods), "pods": pods[:100]}
        return result

    def get_pod_metrics(self, namespace, service):
        return self._get(f"/apis/metrics.k8s.io/v1beta1/namespaces/{_name(namespace)}/pods",
                         labelSelector=f"app.kubernetes.io/name={_name(service)}")

    def get_events(self, namespace, service):
        pods = self.get_pods(namespace, service)
        if not pods["success"]:
            return pods
        names = {p["name"] for p in pods["data"]["pods"]}
        result = self._get(f"/api/v1/namespaces/{_name(namespace)}/events")
        if result["success"]:
            result["data"] = [{"reason": e.get("reason"), "count": e.get("count"),
                "last_at": e.get("lastTimestamp"), "message": str(e.get("message", ""))[:1000]}
                for e in result["data"].get("items", [])
                if e.get("involvedObject", {}).get("name") in names][:50]
        return result

    def describe_pod(self, namespace, pod):
        result = self._get(f"/api/v1/namespaces/{_name(namespace)}/pods/{_name(pod)}")
        if result["success"]:
            data = result["data"]
            result["data"] = {"name": data["metadata"]["name"], "status": data.get("status", {})}
        return result

    def get_replicas(self, namespace, service):
        result = self.get_deployments(namespace)
        if result["success"]:
            result["data"] = [r for r in result["data"] if r["service"] == service]
        return result
