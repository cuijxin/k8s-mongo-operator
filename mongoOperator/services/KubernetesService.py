# Copyright (c) 2018 Ultimaker
# !/usr/bin/env python
# -*- coding: utf-8 -*-
import logging
from typing import Dict, Optional, List

import yaml
from kubernetes import client
from kubernetes.client import Configuration
from kubernetes.client.rest import ApiException

from mongoOperator.Settings import Settings
from mongoOperator.helpers.KubernetesResources import KubernetesResources


class KubernetesService:
    """
    Bundled methods for interacting with the Kubernetes API.
    """

    # Easy definable secret formats.
    OPERATOR_ADMIN_SECRET_FORMAT = "{}-admin-credentials"

    def __init__(self):
        # Create Kubernetes config.
        kubernetes_config = Configuration()
        kubernetes_config.host = Settings.KUBERNETES_SERVICE_HOST
        kubernetes_config.verify_ssl = not Settings.KUBERNETES_SERVICE_SKIP_TLS
        kubernetes_config.debug = Settings.KUBERNETES_SERVICE_DEBUG
        self.api_client = client.ApiClient(configuration=kubernetes_config)

        # Re-usable API client instances.
        self.custom_objects_api = client.CustomObjectsApi(self.api_client)
        self.core_api = client.CoreV1Api(self.api_client)
        self.extensions_api = client.ApiextensionsV1beta1Api(self.api_client)
        self.apps_api = client.AppsV1beta1Api(self.api_client)

    @property
    def apiClient(self):
        return self.api_client

    def createMongoObjectDefinition(self) -> None:
        """Create the custom resource definition."""
        available_crds = {crd.spec.names.kind.lower() for crd in
                          self.extensions_api.list_custom_resource_definition().items}
        if Settings.CUSTOM_OBJECT_RESOURCE_NAME not in available_crds:
            # Create it if our CRD doesn't exists yet.
            logging.info("Custom resource definition {} not found in cluster, creating it...".format(
                    Settings.CUSTOM_OBJECT_RESOURCE_NAME))
            with open("mongo_crd.yaml") as f:
                body = yaml.load(f)
            self.extensions_api.create_custom_resource_definition(body)

    def listMongoObjects(self, **kwargs) -> List[client.V1beta1CustomResourceDefinition]:
        """
        Get all Kubernetes objects of our custom resource type.
        :param kwargs: Additional API flags.
        :return: List of custom resource objects.
        """
        self.createMongoObjectDefinition()
        return self.custom_objects_api.list_cluster_custom_object(Settings.CUSTOM_OBJECT_API_NAMESPACE,
                                                                  Settings.CUSTOM_OBJECT_API_VERSION,
                                                                  Settings.CUSTOM_OBJECT_RESOURCE_NAME,
                                                                  **kwargs)

    def getMongoObject(self, name: str, namespace: str) -> Optional[client.V1beta1CustomResourceDefinition]:
        """
        Get a single Kubernetes Mongo object.
        :param name: The name of the object to get.
        :param namespace: The namespace in which to get the object.
        :return: The custom resource object if existing, otherwise None
        """
        return self.custom_objects_api.get_namespaced_custom_object(Settings.CUSTOM_OBJECT_API_NAMESPACE,
                                                                    Settings.CUSTOM_OBJECT_API_VERSION,
                                                                    namespace,
                                                                    Settings.CUSTOM_OBJECT_RESOURCE_NAME,
                                                                    name)

    def listAllServicesWithLabels(self, label_selector: Dict[str, str] = KubernetesResources.createDefaultLabels())\
            -> List[client.V1Service]:
        """Get all services with the given labels."""
        return self.core_api.list_service_for_all_namespaces(label_selector=label_selector)

    def listAllStatefulSetsWithLabels(self, label_selector: Dict[str, str] = KubernetesResources.createDefaultLabels())\
            -> List[client.V1StatefulSet]:
        """Get all stateful sets with the given labels."""
        return self.apps_api.list_stateful_set_for_all_namespaces(label_selector=label_selector)

    def listAllSecretsWithLabels(self, label_selector: Dict[str, str] = KubernetesResources.createDefaultLabels())\
            -> List[client.V1Secret]:
        """Get al secrets with the given labels."""
        return self.core_api.list_secret_for_all_namespaces(label_selector=label_selector)

    def createOperatorAdminSecret(self, cluster_object: "client.V1beta1CustomResourceDefinition") -> \
            Optional[client.V1Secret]:
        """Create the operator admin secret."""
        secret_data = {"username": "root", "password": KubernetesResources.createRandomPassword()}
        return self.createSecret(self.OPERATOR_ADMIN_SECRET_FORMAT.format(cluster_object.metadata.name),
                                 cluster_object.metadata.namespace, secret_data)

    def deleteOperatorAdminSecret(self, cluster_name: str, namespace: str) -> client.V1Status:
        """
        Delete the operator admin secret.
        :param cluster_name: Name of the cluster.
        :param namespace: Namespace in which to delete the secret.
        :return: The deletion status.
        """
        return self.deleteSecret(self.OPERATOR_ADMIN_SECRET_FORMAT.format(cluster_name), namespace)

    def getSecret(self, secret_name: str, namespace: str) -> Optional[client.V1Secret]:
        """
        Retrieves the secret with the given name.
        :param secret_name: The name of the secret.
        :param namespace: The namespace of the secret.
        :return: The secret object.
        """
        return self.core_api.read_namespaced_secret(secret_name, namespace)

    def createSecret(self, secret_name: str, namespace: str, secret_data: Dict[str, str]) -> Optional[client.V1Secret]:
        """
        Creates a new Kubernetes secret.
        :param secret_name: Unique name of the secret.
        :param namespace: Namespace to add secret to.
        :param secret_data: The data to store in the secret as key/value pair dict.
        :return: The secret if successful, None otherwise.
        """
        try:
            # Create the secret object.
            secret_body = KubernetesResources.createSecret(secret_name, namespace, secret_data)
            secret = self.core_api.create_namespaced_secret(namespace, secret_body)
            logging.info("Created secret %s in namespace %s", secret_name, namespace)
            return secret
        except ApiException as error:
            if error.status == 409:
                # The secret already exists.
                logging.warning("Tried to create a secret that already existed: {} in namespace {}"
                                .format(secret_name, namespace))
                return self.getSecret(secret_name, namespace)
            raise

    def updateSecret(self, secret_name: str, namespace: str, secret_data: Dict[str, str]) -> Optional[client.V1Secret]:
        """
        Updates the given Kubernetes secret.
        :param secret_name: Unique name of the secret.
        :param namespace: Namespace to add secret to.
        :param secret_data: The data to store in the secret as key/value pair dict.
        :return: The secret if successful, None otherwise.
        """
        secret = self.getSecret(secret_name, namespace)
        secret.string_data = secret_data
        return self.core_api.patch_namespaced_secret(secret_name, namespace, secret)

    def deleteSecret(self, name: str, namespace: str) -> client.V1Status:
        """
        Deletes the given Kubernetes secret.
        :param name: Name of the secret to delete.
        :param namespace: Namespace in which to delete the secret.
        :return: The deletion status.
        """
        body = client.V1DeleteOptions()
        return self.core_api.delete_namespaced_secret(name, namespace, body)

    def getService(self, name: str, namespace: str) -> Optional[client.V1Service]:
        """
        Gets an existing service from the cluster.
        :param name: The name of the service to get.
        :param namespace: The namespace in which to get the service.
        :return: The service object if it exists, otherwise None.
        """
        return self.core_api.read_namespaced_service(name, namespace)

    def createService(self, cluster_object: "client.V1beta1CustomResourceDefinition") -> client.V1Service:
        """
        Creates the given cluster.
        :param cluster_object: The cluster object from the YAML file.
        :return: The created service.
        """
        namespace = cluster_object.metadata.namespace
        body = KubernetesResources.createService(cluster_object)
        return self.core_api.create_namespaced_service(namespace, body)

    def updateService(self, cluster_object: "client.V1beta1CustomResourceDefinition") -> client.V1Service:
        """
        Updates the given cluster.
        :param cluster_object: The cluster object from the YAML file.
        :return: The updated service.
        """
        name = cluster_object.metadata.name
        namespace = cluster_object.metadata.namespace
        body = KubernetesResources.createService(cluster_object)
        return self.core_api.patch_namespaced_service(name, namespace, body)

    def deleteService(self, name: str, namespace: str) -> client.V1Status:
        """
        Deletes the service with the given name.
        :param name: The name of the service to delete.
        :param namespace: The namespace in which to delete the service.
        :return: The deletion status.
        """
        body = client.V1DeleteOptions()
        return self.core_api.delete_namespaced_service(name, namespace, body)

    def getStatefulSet(self, name: str, namespace: str) -> Optional[client.V1beta1StatefulSet]:
        """
        Get an existing stateful set from the cluster.
        :param name: The name of the stateful set to get.
        :param namespace: The namespace in which to get the stateful set.
        :return: The stateful set object if existing, otherwise None.
        """
        return self.apps_api.read_namespaced_stateful_set(name, namespace)

    def createStatefulSet(self, cluster_object: "client.V1beta1CustomResourceDefinition") -> client.V1beta1StatefulSet:
        """
        Creates the stateful set for the given cluster object.
        :param cluster_object: The cluster object from the YAML file.
        :return: The created stateful set.
        """
        namespace = cluster_object.metadata.namespace
        body = KubernetesResources.createService(cluster_object)
        return self.apps_api.create_namespaced_stateful_set(namespace, body)

    def updateStatefulSet(self, cluster_object: "client.V1beta1CustomResourceDefinition") -> client.V1beta1StatefulSet:
        """
        Updates the stateful set for the given cluster object.
        :param cluster_object: The cluster object from the YAML file.
        :return: The updated stateful set.
        """
        name = cluster_object.metadata.name
        namespace = cluster_object.metadata.namespace
        body = KubernetesResources.createService(cluster_object)
        return self.apps_api.patch_namespaced_stateful_set(name, namespace, body)

    def deleteStatefulSet(self, name: str, namespace: str) -> bool:
        """
        Deletes the stateful set for the given cluster object.
        :param name: The name of the stateful set to delete.
        :param namespace: The namespace in which to delete the stateful set.
        :return: The updated stateful set.
        """
        body = client.V1DeleteOptions()
        return self.apps_api.delete_namespaced_stateful_set(name, namespace, body)
