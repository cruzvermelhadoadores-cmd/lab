#!/usr/bin/env python3
"""
SSH Tunnel Manager - Gerencia sessões SSH através de VPN (SEM SUDO)
Versão com diagnósticos e timeout fixes
"""
import re
import subprocess
import sys
import os
import json
import argparse
import getpass
from datetime import datetime
import signal
import time

class SSHTunnelManager:
    def __init__(self):
        self.home_dir = os.path.expanduser("~/sshtunnelpy")
        self.sessions_file = os.path.join(self.home_dir, "sessions.json")
        self.current_session_file = os.path.join(self.home_dir, "current_session.txt")
        self.log_dir = self.home_dir
        
        os.makedirs(self.home_dir, exist_ok=True)
        self.sessions = self.load_sessions()
        self.migrate_sessions()
    
    def load_sessions(self):
        if os.path.exists(self.sessions_file):
            with open(self.sessions_file, 'r') as f:
                return json.load(f)
        return []
    
    def save_sessions(self):
        with open(self.sessions_file, 'w') as f:
            json.dump(self.sessions, f, indent=2)
    
    def migrate_sessions(self):
        migrated = False
        for s in self.sessions:
            if 'vpn_host' not in s:
                s['vpn_host'] = s.get('ssh_host', 'unknown')
                s['vpn_port'] = '1194'
                s['vpn_user'] = 'unknown'
                s['vpn_pass'] = ''
                s['vpn_pid'] = None
                migrated = True
            if 'ovpn_file' not in s:
                s['ovpn_file'] = 'unknown.ovpn'
                migrated = True
        
        if migrated:
            self.save_sessions()
            print("⚙️  Sessões migradas para novo formato")
    
    def parse_ovpn(self, file):
        if not os.path.exists(file):
            print(f"❌ Arquivo não encontrado: {file}")
            return None, None
        
        with open(file, 'r') as f:
            content = f.read()
        
        remote = re.search(r'remote\s+(\S+)\s+(\d+)', content)
        host = remote.group(1) if remote else None
        port = remote.group(2) if remote else "1194"
        
        return host, port
    
    def parse_ssh_string(self, ssh_str):
        pattern = r'([^@]+)@([^:]+)(?::(\d+))?(?::(.+))?'
        match = re.match(pattern, ssh_str)
        
        if match:
            user = match.group(1)
            host = match.group(2)
            port = match.group(3) or "22"
            password = match.group(4)
            return user, host, port, password
        return None, None, "22", None
    
    def check_sshpass(self):
        try:
            subprocess.run(['which', 'sshpass'], check=True, capture_output=True)
            return True
        except:
            return False
    
    def diagnose_network(self, session):
        """Diagnóstico de rede"""
        print("\n🔍 Diagnóstico de Rede:")
        
        # 1. Verificar interface VPN
        print("  1️⃣ Verificando interface tun0...")
        tun_check = subprocess.run(['ip', 'addr', 'show', 'tun0'], 
                                  capture_output=True, text=True)
        if tun_check.returncode == 0:
            print("     ✅ tun0 ativa")
            # Extrair IP
            ip_match = re.search(r'inet\s+(\d+\.\d+\.\d+\.\d+)', tun_check.stdout)
            if ip_match:
                print(f"     📍 IP VPN: {ip_match.group(1)}")
        else:
            print("     ❌ tun0 não encontrada!")
            return False
        
        # 2. Verificar roteamento
        print(f"\n  2️⃣ Verificando rota para {session['ssh_host']}...")
        route_check = subprocess.run(['ip', 'route', 'get', session['ssh_host']], 
                                    capture_output=True, text=True)
        if route_check.returncode == 0:
            print(f"     ✅ Rota existe")
            if 'tun0' in route_check.stdout:
                print(f"     ✅ Passa pela VPN (tun0)")
            else:
                print(f"     ⚠️  NÃO passa pela VPN!")
                print(f"     {route_check.stdout.strip()}")
        else:
            print(f"     ❌ Sem rota para {session['ssh_host']}")
            return False
        
        # 3. Ping test
        print(f"\n  3️⃣ Testando ping para {session['ssh_host']}...")
        ping = subprocess.run(['ping', '-c', '2', '-W', '3', session['ssh_host']], 
                             capture_output=True, text=True)
        if ping.returncode == 0:
            print(f"     ✅ Host responde ping")
        else:
            print(f"     ⚠️  Host não responde ping (pode estar bloqueado)")
        
        # 4. Port test
        print(f"\n  4️⃣ Testando porta SSH {session['ssh_port']}...")
        nc_check = subprocess.run(['timeout', '5', 'nc', '-zv', 
                                  session['ssh_host'], session['ssh_port']], 
                                 capture_output=True, text=True)
        if nc_check.returncode == 0 or 'succeeded' in nc_check.stderr.lower():
            print(f"     ✅ Porta {session['ssh_port']} acessível")
            return True
        else:
            print(f"     ❌ Porta {session['ssh_port']} BLOQUEADA ou fechada")
            print(f"     Erro: {nc_check.stderr.strip()}")
            return False
    
    def init_session(self, ovpn_file, vpn_user=None, vpn_pass=None, ssh_string=None):
        print(f"🔍 Analisando {ovpn_file}...")
        
        vpn_host, vpn_port = self.parse_ovpn(ovpn_file)
        if not vpn_host:
            print("❌ Não consegui ler o arquivo .ovpn")
            return False
        
        print(f"📡 Servidor VPN: {vpn_host}:{vpn_port}")
        
        ssh_user = None
        ssh_host = None
        ssh_port = "22"
        ssh_pass = None
        
        if ssh_string:
            ssh_user, ssh_host, ssh_port, ssh_pass = self.parse_ssh_string(ssh_string)
            print(f"🔐 SSH: {ssh_user}@{ssh_host}:{ssh_port}")
        
        if not vpn_user:
            print(f"\n📋 Credenciais VPN:")
            vpn_user = input("  Usuário VPN: ")
        if not vpn_pass:
            vpn_pass = getpass.getpass("  Senha VPN: ")
        
        if ssh_string and not ssh_host:
            print(f"\n❌ Formato SSH inválido. Use: user@host:port:password")
            return False
        
        if not ssh_host:
            print(f"\n📋 Credenciais SSH (servidor atrás da VPN):")
            ssh_host = input("  Host SSH: ")
            ssh_user = input(f"  Usuário SSH [{ssh_host}]: ") or getpass.getuser()
            ssh_port = input("  Porta SSH [22]: ") or "22"
        
        if not ssh_pass:
            ssh_pass = getpass.getpass(f"  Senha SSH [{ssh_user}@{ssh_host}]: ")
        
        session = {
            'id': len(self.sessions) + 1,
            'name': f"session_{len(self.sessions) + 1}",
            'created': datetime.now().isoformat(),
            'ovpn_file': os.path.abspath(ovpn_file),
            'vpn_host': vpn_host,
            'vpn_port': vpn_port,
            'vpn_user': vpn_user,
            'vpn_pass': vpn_pass,
            'ssh_host': ssh_host,
            'ssh_port': ssh_port,
            'ssh_user': ssh_user,
            'ssh_pass': ssh_pass,
            'vpn_pid': None,
            'ssh_pid': None,
            'status': 'disconnected',
            'control_socket': os.path.join(self.home_dir, f"ssh_control_{len(self.sessions) + 1}")
        }
        
        self.sessions.append(session)
        self.save_sessions()
        
        print(f"\n✅ Sessão {session['id']} criada!")
        print(f"📦 VPN: {vpn_user}@{vpn_host}:{vpn_port}")
        print(f"🔐 SSH: {ssh_user}@{ssh_host}:{ssh_port}")
        
        connect_now = input("\n🚀 Conectar agora? [S/n]: ").strip().lower()
        if connect_now != 'n':
            return self.connect_session(session['id'])
        
        return True
    
    def connect_session(self, session_id, skip_vpn=False):
        """Conecta VPN e SSH"""
        session = self.get_session(session_id)
        if not session:
            print(f"❌ Sessão {session_id} não encontrada")
            return False
        
        print(f"\n🔒 Conectando sessão {session_id}...")
        
        # Verificar sshpass
        if not self.check_sshpass():
            print("\n❌ sshpass não está instalado!")
            print("   Instale com: sudo apt install sshpass")
            return False
        
        # Logs
        vpn_log = os.path.join(self.log_dir, f"vpn_{session_id}.log")
        ssh_log = os.path.join(self.log_dir, f"ssh_{session_id}.log")
        
        # Etapa 1: Conectar VPN
        if not skip_vpn:
            try:
                subprocess.run(['which', 'openvpn'], check=True, capture_output=True)
                has_openvpn = True
            except:
                has_openvpn = False
                print("⚠️  OpenVPN não encontrado")
            
            if has_openvpn:
                print("📡 Conectando VPN...")
                
                auth_file = os.path.join(self.home_dir, f"vpn_auth_{session_id}.txt")
                with open(auth_file, 'w') as f:
                    f.write(f"{session['vpn_user']}\n{session['vpn_pass']}\n")
                os.chmod(auth_file, 0o600)
                
                vpn_cmd = [
                    'sudo', 'openvpn',
                    '--config', session['ovpn_file'],
                    '--auth-user-pass', auth_file,
                    '--daemon',
                    '--log', vpn_log,
                    '--writepid', os.path.join(self.home_dir, f"vpn_{session_id}.pid")
                ]
                
                try:
                    result = subprocess.run(vpn_cmd, capture_output=True, text=True, timeout=10)
                    
                    if result.returncode == 0:
                        print("⏳ Aguardando VPN conectar (15s)...")
                        
                        for i in range(15):
                            time.sleep(1)
                            check = subprocess.run(['ip', 'addr', 'show', 'tun0'], 
                                                 capture_output=True, text=True)
                            if check.returncode == 0:
                                print("✅ VPN conectada!")
                                
                                pid_file = os.path.join(self.home_dir, f"vpn_{session_id}.pid")
                                if os.path.exists(pid_file):
                                    with open(pid_file, 'r') as f:
                                        session['vpn_pid'] = f.read().strip()
                                
                                break
                        else:
                            print("⚠️  VPN demorou demais")
                    else:
                        print(f"⚠️  Erro VPN: {result.stderr}")
                
                except Exception as e:
                    print(f"⚠️  Erro ao conectar VPN: {e}")
                
                finally:
                    if os.path.exists(auth_file):
                        os.remove(auth_file)
        else:
            print("⏩ Pulando reconexão VPN (já conectada)")
        
        # Diagnóstico antes de SSH
        if not self.diagnose_network(session):
            print("\n❌ Diagnóstico falhou! Host SSH não acessível pela VPN")
            print("   Verifique:")
            print("   1. VPN está realmente conectada?")
            print("   2. Roteamento está correto?")
            print(f"   3. {session['ssh_host']} está acessível?")
            print(f"\n📝 Ver logs: tail -f {vpn_log}")
            return False
        
        # Etapa 2: Conectar SSH
        print(f"\n🔐 Conectando SSH a {session['ssh_host']}...")
        
        # Criar config SSH
        ssh_config = os.path.join(self.home_dir, f"ssh_config_{session_id}")
        with open(ssh_config, 'w') as f:
            f.write(f"Host tunnel{session_id}\n")
            f.write(f"    HostName {session['ssh_host']}\n")
            f.write(f"    Port {session['ssh_port']}\n")
            f.write(f"    User {session['ssh_user']}\n")
            f.write(f"    StrictHostKeyChecking no\n")
            f.write(f"    UserKnownHostsFile /dev/null\n")
            f.write(f"    ServerAliveInterval 60\n")
            f.write(f"    ServerAliveCountMax 3\n")
            f.write(f"    ConnectTimeout 15\n")
            f.write(f"    ControlMaster auto\n")
            f.write(f"    ControlPath {session['control_socket']}\n")
            f.write(f"    ControlPersist yes\n")
        
        # Comando SSH com sshpass e timeout
        ssh_cmd = [
            'timeout', '20',  # Timeout externo de 20s
            'sshpass', '-p', session['ssh_pass'],
            'ssh', '-f', '-N', '-v',  # -v para debug
            '-F', ssh_config,
            '-o', 'ExitOnForwardFailure=yes',
            f"tunnel{session_id}"
        ]
        
        print("⏳ Conectando SSH (timeout 20s)...")
        
        try:
            # Salvar stderr para debug
            result = subprocess.run(ssh_cmd, capture_output=True, text=True)
            
            # Salvar log SSH
            with open(ssh_log, 'w') as f:
                f.write("=== SSH Connection Log ===\n")
                f.write(f"STDOUT:\n{result.stdout}\n")
                f.write(f"STDERR:\n{result.stderr}\n")
                f.write(f"Return code: {result.returncode}\n")
            
            # Verificar conexão SSH
            time.sleep(2)
            check_cmd = [
                'ssh', '-F', ssh_config,
                '-O', 'check',
                f"tunnel{session_id}"
            ]
            
            check_result = subprocess.run(check_cmd, capture_output=True, text=True)
            
            if "Master running" in check_result.stderr or check_result.returncode == 0:
                session['status'] = 'connected'
                session['connected_at'] = datetime.now().isoformat()
                
                with open(self.current_session_file, 'w') as f:
                    f.write(str(session_id))
                
                self.save_sessions()
                
                print(f"\n✅ Sessão {session_id} totalmente conectada!")
                print(f"📡 VPN: {session['vpn_host']}")
                print(f"🔐 SSH: {session['ssh_user']}@{session['ssh_host']}")
                print(f"📝 Logs: {ssh_log}")
                print(f"\n💡 Use: ./tunn --cmd 'seu comando'")
                
                return True
            else:
                print(f"\n❌ Falha ao conectar SSH")
                print(f"   Erro: {check_result.stderr}")
                print(f"   SSH debug: {ssh_log}")
                print(f"\n🔍 Ver detalhes: cat {ssh_log}")
                return False
                
        except subprocess.TimeoutExpired:
            print("\n❌ Timeout SSH (20s)")
            print(f"   Ver logs: cat {ssh_log}")
            return False
        except Exception as e:
            print(f"\n❌ Erro SSH: {e}")
            print(f"   Ver logs: cat {ssh_log}")
            return False
    
    def execute_command(self, cmd):
        session = self.get_current_session()
        if not session:
            print("❌ Nenhuma sessão ativa. Use: ./tunn --c <id>")
            return False
        
        if session['status'] != 'connected':
            print(f"❌ Sessão {session['id']} não está conectada")
            return False
        
        print(f"🔧 Executando: {cmd}")
        print(f"   na sessão {session['id']} ({session['ssh_user']}@{session['ssh_host']})\n")
        
        output_log = os.path.join(self.log_dir, "output.log")
        ssh_config = os.path.join(self.home_dir, f"ssh_config_{session['id']}")
        
        ssh_cmd = [
            'ssh',
            '-F', ssh_config,
            f"tunnel{session['id']}",
            cmd
        ]
        
        try:
            result = subprocess.run(ssh_cmd, capture_output=True, text=True, timeout=60)
            
            output = result.stdout
            if result.stderr:
                output += "\n[STDERR]\n" + result.stderr
            
            print("="*60)
            print(output)
            print("="*60 + "\n")
            
            with open(output_log, 'a') as f:
                timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                f.write(f"\n[{timestamp}] Session {session['id']}: {cmd}\n")
                f.write(output)
                f.write("\n" + "="*60 + "\n")
            
            print(f"📝 Output: {output_log}")
            return True
            
        except subprocess.TimeoutExpired:
            print("⏱️  Timeout (60s)")
            return False
        except Exception as e:
            print(f"❌ Erro: {e}")
            return False
    
    def stop_session(self):
        session = self.get_current_session()
        if not session:
            print("❌ Nenhuma sessão ativa")
            return False
        
        print(f"🛑 Parando sessão {session['id']}...")
        
        ssh_config = os.path.join(self.home_dir, f"ssh_config_{session['id']}")
        if os.path.exists(ssh_config):
            stop_ssh = [
                'ssh', '-F', ssh_config,
                '-O', 'exit',
                f"tunnel{session['id']}"
            ]
            
            try:
                subprocess.run(stop_ssh, capture_output=True, timeout=5)
                print("✅ SSH desconectado")
            except:
                pass
        
        if session.get('vpn_pid'):
            try:
                subprocess.run(['sudo', 'kill', session['vpn_pid']], 
                             capture_output=True, timeout=5)
                print("✅ VPN desconectada")
            except:
                pass
        
        session['status'] = 'disconnected'
        session['vpn_pid'] = None
        session['ssh_pid'] = None
        self.save_sessions()
        
        for f in [ssh_config, session.get('control_socket', ''), self.current_session_file]:
            if f and os.path.exists(f):
                try:
                    os.remove(f)
                except:
                    pass
        
        print(f"✅ Sessão {session['id']} parada")
        return True
    
    def list_sessions(self):
        if not self.sessions:
            print("📭 Nenhuma sessão salva")
            return
        
        current_id = self.get_current_session_id()
        
        print("\n📋 Sessões SSH Tunnel:\n")
        print(f"{'ID':<4} {'Status':<12} {'VPN':<30} {'SSH':<30} {'Criado':<20}")
        print("-" * 100)
        
        for s in self.sessions:
            status = s.get('status', 'unknown')
            if s['id'] == current_id:
                status += " ⭐"
            
            vpn = f"{s.get('vpn_host', 'N/A')}:{s.get('vpn_port', 'N/A')}"
            ssh = f"{s.get('ssh_user', 'N/A')}@{s.get('ssh_host', 'N/A')}:{s.get('ssh_port', 'N/A')}"
            created = s.get('created', 'N/A')[:19]
            
            print(f"{s['id']:<4} {status:<12} {vpn:<30} {ssh:<30} {created:<20}")
        
        print()
    
    def remove_session(self, session_id):
        session = self.get_session(session_id)
        if not session:
            print(f"❌ Sessão {session_id} não encontrada")
            return False
        
        if session.get('status') == 'connected':
            current = self.get_current_session()
            if current and current['id'] == session_id:
                self.stop_session()
        
        self.sessions = [s for s in self.sessions if s['id'] != session_id]
        self.save_sessions()
        
        print(f"✅ Sessão {session_id} removida")
        return True
    
    def get_session(self, session_id):
        for s in self.sessions:
            if s['id'] == session_id:
                return s
        return None
    
    def get_current_session_id(self):
        if os.path.exists(self.current_session_file):
            with open(self.current_session_file, 'r') as f:
                return int(f.read().strip())
        return None
    
    def get_current_session(self):
        sid = self.get_current_session_id()
        return self.get_session(sid) if sid else None

def main():
    parser = argparse.ArgumentParser(
        description='SSH Tunnel Manager via VPN',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Exemplos:
  ./tunn --init vpn.ovpn --user="USER" --pass="PASS" --ssh="user@host:22:pass"
  ./tunn --cmd "ls -la"
  ./tunn --list
  ./tunn --c 1
  ./tunn --c 1 --skip-vpn   # Reconectar SSH sem reconectar VPN
  ./tunn --stop
        """
    )
    
    parser.add_argument('--init', metavar='OVPN', help='Criar sessão')
    parser.add_argument('--user', help='Usuário VPN')
    parser.add_argument('--pass', dest='password', help='Senha VPN')
    parser.add_argument('--ssh', help='SSH: user@host:port:password')
    parser.add_argument('--cmd', help='Executar comando')
    parser.add_argument('--stop', action='store_true', help='Parar sessão')
    parser.add_argument('--list', action='store_true', help='Listar')
    parser.add_argument('--c', type=int, metavar='ID', help='Conectar')
    parser.add_argument('--skip-vpn', action='store_true', help='Pular reconexão VPN')
    parser.add_argument('--rm', type=int, metavar='ID', help='Remover')
    
    args = parser.parse_args()
    
    manager = SSHTunnelManager()
    
    if args.init:
        manager.init_session(args.init, args.user, args.password, args.ssh)
    elif args.cmd:
        manager.execute_command(args.cmd)
    elif args.stop:
        manager.stop_session()
    elif args.list:
        manager.list_sessions()
    elif args.c:
        manager.connect_session(args.c, skip_vpn=args.skip_vpn)
    elif args.rm:
        manager.remove_session(args.rm)
    else:
        parser.print_help()

if __name__ == "__main__":
    main()