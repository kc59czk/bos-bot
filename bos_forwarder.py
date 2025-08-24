import socket
import threading
import time
import winreg
from typing import Tuple
import logging
from datetime import datetime

class NOL3PortForwarder:
    def __init__(self, local_host: str = '127.0.0.1', remote_host: str = '127.0.0.1'):
        self.local_host = local_host
        self.remote_host = remote_host
        self.sync_port = None
        self.async_port = None
        self.forwarding_threads = []
        self.running = False
        
        # Setup logging
        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s - %(levelname)s - %(message)s',
            handlers=[
                logging.FileHandler('nol3_port_forwarder.log'),
                logging.StreamHandler()
            ]
        )
        self.log = logging.getLogger()
    
    def _get_ports_from_registry(self) -> bool:
        """Read ports from Windows registry"""
        try:
            key_path = r"Software\COMARCH S.A.\NOL3\7\Settings"
            registry_key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_READ)
            self.sync_port, _ = winreg.QueryValueEx(registry_key, "nca_psync")
            self.async_port, _ = winreg.QueryValueEx(registry_key, "nca_pasync")
            self.sync_port = int(self.sync_port)
            self.async_port = int(self.async_port)
            winreg.CloseKey(registry_key)
            self.log.info(f"Read ports from registry: Sync={self.sync_port}, Async={self.async_port}")
            return True
        except FileNotFoundError:
            self.log.error("ERROR: Registry key for COMARCH NOL3 not found.")
            return False
        except Exception as e:
            self.log.error(f"ERROR reading registry: {e}")
            return False
    
    def forward_port(self, local_port: int, remote_port: int, name: str = "Port"):
        """Forward traffic from local port to remote port"""
        def handle_client(local_sock: socket.socket):
            try:
                # Connect to remote server
                remote_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                remote_sock.connect((self.remote_host, remote_port))
                self.log.info(f"{name}: Connected to remote {self.remote_host}:{remote_port}")
                
                # Start bidirectional forwarding
                threads = [
                    threading.Thread(target=self.forward_data, args=(local_sock, remote_sock, f"{name} Local→Remote")),
                    threading.Thread(target=self.forward_data, args=(remote_sock, local_sock, f"{name} Remote→Local"))
                ]
                
                for thread in threads:
                    thread.daemon = True
                    thread.start()
                
                # Wait for threads to complete
                for thread in threads:
                    thread.join()
                    
            except Exception as e:
                self.log.error(f"{name}: Connection error: {e}")
            finally:
                local_sock.close()
                if 'remote_sock' in locals():
                    remote_sock.close()
        
        # Create local server
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind((self.local_host, local_port))
        server.listen(5)
        
        self.log.info(f"{name}: Forwarding {self.local_host}:{local_port} → {self.remote_host}:{remote_port}")
        
        while self.running:
            try:
                client_sock, client_addr = server.accept()
                self.log.info(f"{name}: New connection from {client_addr}")
                
                # Handle client in separate thread
                client_thread = threading.Thread(target=handle_client, args=(client_sock,))
                client_thread.daemon = True
                client_thread.start()
                
            except socket.error as e:
                if self.running:
                    self.log.error(f"{name}: Socket error: {e}")
                break
    
    def forward_data(self, source: socket.socket, destination: socket.socket, name: str):
        """Forward data between two sockets"""
        try:
            while self.running:
                data = source.recv(4096)
                if not data:
                    break
                destination.sendall(data)
                self.log.debug(f"{name}: Forwarded {len(data)} bytes")
        except socket.error:
            pass  # Connection closed
        except Exception as e:
            self.log.error(f"{name}: Data forwarding error: {e}")
    
    def start_forwarding(self):
        """Start port forwarding"""
        if not self._get_ports_from_registry():
            self.log.error("Failed to get ports from registry. Cannot start forwarding.")
            return False
        
        self.running = True
        
        # Start forwarding for both ports
        ports_to_forward = [
            (self.sync_port, self.sync_port, "SYNC"),
            (self.async_port, self.async_port, "ASYNC")
        ]
        
        for local_port, remote_port, name in ports_to_forward:
            thread = threading.Thread(
                target=self.forward_port, 
                args=(local_port, remote_port, name)
            )
            thread.daemon = True
            thread.start()
            self.forwarding_threads.append(thread)
        
        self.log.info("Port forwarding started successfully!")
        self.log.info(f"Local listening on: {self.local_host}")
        self.log.info(f"Forwarding to: {self.remote_host}")
        self.log.info(f"Ports: Sync={self.sync_port}, Async={self.async_port}")
        
        return True
    
    def stop_forwarding(self):
        """Stop port forwarding"""
        self.running = False
        self.log.info("Stopping port forwarding...")
        
        # Give threads time to exit
        time.sleep(1)
        
        self.log.info("Port forwarding stopped.")

def main():
    import argparse
    
    parser = argparse.ArgumentParser(description='COMARCH NOL3 Port Forwarder')
    parser.add_argument('--remote', '-r', default='127.0.0.1', 
                       help='Remote host IP address (default: 127.0.0.1)')
    parser.add_argument('--local', '-l', default='0.0.0.0',
                       help='Local interface to bind to (default: 0.0.0.0 - all interfaces)')
    parser.add_argument('--sync-port', type=int,
                       help='Override sync port from registry')
    parser.add_argument('--async-port', type=int,
                       help='Override async port from registry')
    
    args = parser.parse_args()
    
    # Create forwarder instance
    forwarder = NOL3PortForwarder(local_host=args.local, remote_host=args.remote)
    
    # Override ports if specified
    if args.sync_port:
        forwarder.sync_port = args.sync_port
    if args.async_port:
        forwarder.async_port = args.async_port
    
    try:
        # Start forwarding
        if forwarder.start_forwarding():
            print("Port forwarder running. Press Ctrl+C to stop.")
            
            # Keep main thread alive
            while True:
                time.sleep(1)
                
    except KeyboardInterrupt:
        print("\nShutting down...")
    finally:
        forwarder.stop_forwarding()

if __name__ == "__main__":
    main()
