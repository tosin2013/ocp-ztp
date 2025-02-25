import yaml
import os
import argparse
import subprocess
import json

def generate_namespace_yaml(cluster_name, output_dir):
    """Generates the 00-namespace.yaml file."""

    namespace_yaml = {
        'apiVersion': 'v1',
        'kind': 'Namespace',
        'metadata': {
            'name': cluster_name
        }
    }
    output_file = os.path.join(output_dir, "00-namespace.yaml")
    with open(output_file, 'w') as f:
        yaml.dump(namespace_yaml, f, sort_keys=False)
    print(f"Generated {output_file}")

def generate_agentclusterinstall_yaml(cluster_config, nodes_config, output_dir, openshift_version, ssh_key_path=None):
    """Generates the 01-agentclusterinstall.yaml file."""

    cluster_name = cluster_config.get('cluster_name')
    if not cluster_name:
        print("Error: cluster_name not found in cluster.yml and not provided via --cluster-name")
        return
        
    # Get location from cluster name or use default
    location = cluster_name.split('-')[-1].capitalize() if '-' in cluster_name else 'Factory'

    agentclusterinstall_yaml = {
        'apiVersion': 'extensions.hive.openshift.io/v1beta1',
        'kind': 'AgentClusterInstall',
        'metadata': {
            'name': cluster_name,
            'namespace': cluster_name,
            'labels': {
                'agentclusterinstalls.extensions.hive.openshift.io/location': location
            },
            'annotations': {
              'agent-install.openshift.io/install-config-overrides': '{"networking":{"networkType":"OVNKubernetes"}}'
            }
        },
        'spec': {
            'clusterDeploymentRef': {
                'name': cluster_name
            },
            'imageSetRef': {
                'name': f'openshift-v{openshift_version}'
            },
            'networking': {
                'clusterNetwork': [{'cidr': cluster_config.get('cluster_network_cidr'), 'hostPrefix': cluster_config.get('cluster_network_host_prefix')}],
                'serviceNetwork': cluster_config.get('service_network_cidrs', []),  # Array of strings
                'machineNetwork': [{'cidr': cidr} for cidr in cluster_config.get('machine_network_cidrs', [])]
            },
            'provisionRequirements': {
                'controlPlaneAgents': sum(1 for node in nodes_config.get('nodes', []) if node.get('role') == 'master')
            },
            'sshPublicKey': ''  # Initialize as empty string
        }
    }

    # Handle SSH Public Key
    if ssh_key_path:
        try:
            with open(ssh_key_path, 'r') as f:
                agentclusterinstall_yaml['spec']['sshPublicKey'] = f.read().strip()
        except FileNotFoundError:
            print(f"Warning: SSH public key file not found at {ssh_key_path}.  Skipping SSH key.")
    else:
        print("Warning: --ssh-key not provided. Generating a new SSH key pair.")
        # Generate a new SSH key pair
        private_key_path = os.path.join(output_dir, "ssh-privatekey")
        try:
          # Use -N "" for empty passphrase
          subprocess.run(['ssh-keygen', '-t', 'rsa', '-b', '4096', '-f', private_key_path, '-N', ''], check=True, capture_output=True)
          with open(private_key_path + ".pub", 'r') as f:
            agentclusterinstall_yaml['spec']['sshPublicKey'] = f.read().strip()
        except subprocess.CalledProcessError as e:
          print (f"Error generating SSH key: {e}")
          return
        except FileNotFoundError as e:
          print (f"Error: Could not generate keypair: {e}")
          return
        print(f"Generated SSH key pair: {private_key_path}, {private_key_path}.pub")


    output_file = os.path.join(output_dir, "01-agentclusterinstall.yaml")
    with open(output_file, 'w') as f:
        yaml.dump(agentclusterinstall_yaml, f, sort_keys=False)
    print(f"Generated {output_file}")

def generate_clusterdeployment_yaml(cluster_name, cluster_config, output_dir):
    """Generates the 02-clusterdeployment.yaml file."""

    # Get location from cluster name or use default
    location = cluster_name.split('-')[-1].capitalize() if '-' in cluster_name else 'Factory'

    clusterdeployment_yaml = {
        'apiVersion': 'hive.openshift.io/v1',
        'kind': 'ClusterDeployment',
        'metadata': {
            'name': cluster_name,
            'namespace': cluster_name
        },
        'spec': {
            'clusterName': cluster_name,
            'baseDomain': cluster_config.get('base_domain'),
            'clusterInstallRef': {
                'group': 'extensions.hive.openshift.io',
                'kind': 'AgentClusterInstall',
                'name': cluster_name,
                'version': 'v1beta1'
            },
            'platform': {
                'agentBareMetal': {
                    'agentSelector': {
                        'matchLabels': {
                            'agentclusterinstalls.extensions.hive.openshift.io/location': location
                        }
                    }
                }
            },
            'pullSecretRef': {
                'name': 'assisted-deployment-pull-secret'
            }
        }
    }
    output_file = os.path.join(output_dir, "02-clusterdeployment.yaml")
    with open(output_file, 'w') as f:
        yaml.dump(clusterdeployment_yaml, f, sort_keys=False)
    print(f"Generated {output_file}")
    
def generate_baremetalhost_yaml(cluster_name, nodes_config, output_dir):
    """Generates the 05-baremetalhost.yaml file with BMC secrets."""
    configs = []
    node_count = 1
    
    for node in nodes_config.get('nodes', []):
        # Get role and node number
        role = node.get('role', 'worker')
        node_name = f"{cluster_name}-{role}-{node_count}"
        
        # Get BMC info
        bmc = node.get('bmc', {})
        # Get first interface's MAC for boot MAC
        boot_mac = node.get('interfaces', [{}])[0].get('mac_address', '')

        # Generate BareMetalHost
        bmh = {
            'apiVersion': 'metal3.io/v1alpha1',
            'kind': 'BareMetalHost',
            'metadata': {
                'name': node_name,
                'namespace': cluster_name,
                'labels': {
                    'infraenvs.agent-install.openshift.io': cluster_name,
                    'agent-install.openshift.io/role': role
                },
                'annotations': {
                    'inspect.metal3.io': 'disabled',
                    'bmac.agent-install.openshift.io/hostname': node_name
                }
            },
            'spec': {
                'online': True,
                'bmc': {
                    'address': bmc.get('address', ''),
                    'credentialsName': f"{node_name}-secret",
                    'disableCertificateVerification': True
                },
                'bootMACAddress': boot_mac,
                'automatedCleaningMode': 'disabled',
                'hardwareProfile': 'libvirt'
            }
        }
        configs.append(bmh)
        
        # Generate corresponding BMC secret with actual credentials
        import base64
        secret = {
            'apiVersion': 'v1',
            'kind': 'Secret',
            'metadata': {
                'name': f"{node_name}-secret",
                'namespace': cluster_name
            },
            'data': {
                'username': base64.b64encode(bmc.get('username', '').encode()).decode(),
                'password': base64.b64encode(bmc.get('password', '').encode()).decode()
            },
            'type': 'Opaque'
        }
        configs.append(secret)
        node_count += 1
    
    if not configs:
        print("Warning: No nodes with hostname found. Skipping 05-baremetalhost.yaml generation.")
        return
        
    output_file = os.path.join(output_dir, "05-baremetalhost.yaml")
    with open(output_file, 'w') as f:
        yaml.dump_all(configs, f, sort_keys=False, explicit_start=True)
    print(f"Generated {output_file}")

def generate_nmstateconfig_yaml(nodes_config, output_dir, cluster_name, cluster_config):
    """Generates the 03-nmstateconfig.yaml file."""
    
    configs = []
    node_count = 1

    for node in nodes_config.get('nodes', []):
        if not node.get('networkConfig'):
            continue

        # Determine role and generate standard name
        role = node.get('role', 'worker')
        base_name = f"{cluster_name}-{role}-{node_count}"
        node_name = f"{cluster_name}-nmstate-{base_name}"

        # Convert node's network config to correct NMState format
        interfaces = []
        for iface in node.get('networkConfig', {}).get('interfaces', []):
            iface_config = {
                'name': iface.get('name'),
                'state': iface.get('state', 'up')
            }

            # Handle different interface types
            if iface.get('type') == 'bond':
                iface_config['type'] = 'bond'
                iface_config['link-aggregation'] = iface.get('link-aggregation', {})
            elif 'vlan' in iface:
                iface_config['type'] = 'vlan'
                iface_config['vlan'] = iface['vlan']
            else:
                iface_config['type'] = 'ethernet'

            # Add IPv4 configuration if present
            if 'ipv4' in iface:
                iface_config['ipv4'] = iface['ipv4']

            interfaces.append(iface_config)

        # Build network config with DNS settings from cluster config
        network_config = {
            'interfaces': interfaces,
            'dns-resolver': {
                'config': {
                    'search': cluster_config.get('dns_search_domains', []),
                    'server': cluster_config.get('dns_servers', [])
                }
            }
        }

        # Add routes if present
        if routes := node.get('networkConfig', {}).get('routes', {}):
            network_config['routes'] = {
                'config': routes.get('config', [])
            }

        nmstate_yaml = {
            'apiVersion': 'agent-install.openshift.io/v1beta1',
            'kind': 'NMStateConfig',
            'metadata': {
                'name': node_name,
                'namespace': cluster_name,
                'labels': {
                    'cluster-name': f"{cluster_name}-nmstate",
                    'agent-install.openshift.io/role': role,
                    'cluster-resource': base_name
                }
            },
            'spec': {
                'config': network_config,
                'interfaces': []
            }
        }

        # Add interfaces with their MAC addresses
        for iface in node.get('interfaces', []):
            if iface.get('mac_address'):
                nmstate_yaml['spec']['interfaces'].append({
                    'name': iface.get('name'),
                    'macAddress': iface.get('mac_address')
                })

        configs.append(nmstate_yaml)
        node_count += 1

    if not configs:
        print("Warning: No networkConfig found in nodes. Skipping 03-nmstateconfig.yaml generation.")
        return

    output_file = os.path.join(output_dir, "03-nmstateconfig.yaml")
    with open(output_file, 'w') as f:
        yaml.dump_all(configs, f, sort_keys=False, explicit_start=True)
    print(f"Generated {output_file}")

def generate_spokeinfraenv_yaml(cluster_name, ssh_key_path, cluster_config, output_dir):
    """Generates the 04-spokeinfraenv.yaml file."""
    
    location = cluster_name.split('-')[-1].capitalize() if '-' in cluster_name else 'TODO'
    
    # Get SSH key content
    try:
        with open(ssh_key_path, 'r') as f:
            ssh_key = f.read().strip()
    except (FileNotFoundError, TypeError):
        ssh_key = ""  # Empty if no key provided
        print("Warning: No SSH key available for InfraEnv config")
    
    infraenv = {
        'apiVersion': 'agent-install.openshift.io/v1beta1',
        'kind': 'InfraEnv',
        'metadata': {
            'labels': {
                'agentclusterinstalls.extensions.hive.openshift.io/location': location,
                'networkType': 'static'
            },
            'name': cluster_name,
            'namespace': cluster_name
        },
        'spec': {
            'clusterRef': {
                'name': cluster_name,
                'namespace': cluster_name
            },
            'sshAuthorizedKey': ssh_key,
            'agentLabelSelector': {
                'matchLabels': {
                    'agentclusterinstalls.extensions.hive.openshift.io/location': location
                }
            },
            'pullSecretRef': {
                'name': 'assisted-deployment-pull-secret'
            },
            'nmStateConfigLabelSelector': {
                'matchLabels': {
                    'cluster-name': f"{cluster_name}-nmstate"
                }
            }
        }
    }
    
    # Add NTP sources if available
    if ntp_servers := cluster_config.get('ntp_servers', []):
        infraenv['spec']['additionalNTPSources'] = ntp_servers
    
    output_file = os.path.join(output_dir, "04-spokeinfraenv.yaml")
    with open(output_file, 'w') as f:
        yaml.dump(infraenv, f, sort_keys=False)
    print(f"Generated {output_file}")

def generate_kusterlet_yaml(cluster_name, output_dir):
    """Generates the 07-kusterlet.yaml file."""
    
    configs = [
        {
            'apiVersion': 'cluster.open-cluster-management.io/v1',
            'kind': 'ManagedCluster',
            'metadata': {
                'labels': {
                    'cloud': 'hybrid',
                    'name': cluster_name
                },
                'name': cluster_name
            },
            'spec': {
                'hubAcceptsClient': True
            }
        },
        {
            'apiVersion': 'agent.open-cluster-management.io/v1',
            'kind': 'KlusterletAddonConfig',
            'metadata': {
                'name': cluster_name,
                'namespace': cluster_name
            },
            'spec': {
                'clusterName': cluster_name,
                'clusterNamespace': cluster_name,
                'clusterLabels': {
                    'cloud': 'hybrid'
                },
                'applicationManager': {
