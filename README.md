# fast-ticket

An API-first integration layer and backend for osTicket.

## Quick Setup

This project uses Docker to provide a zero-configuration, out-of-the-box development environment. Everything you need (MariaDB, osTicket, FastAPI backend, and the integration bridge) is spun up automatically.

### Prerequisites
* Docker and Docker Compose
* Make

### 1. Boot the Stack
To generate the necessary secure credentials and boot up the entire stack, run:
```bash
make start
```
*This command will automatically duplicate `.env.example` to `.env`, generate secure random passwords and keys, and build the Docker containers. The osTicket installation is performed completely headless, bypassing the manual web wizard.*

### 2. Access the Services
Once the containers are running and healthy (usually takes 1-2 minutes on first boot), the following services are available:

* **FastAPI Backend**: [http://localhost:8000](http://localhost:8000)
  * Swagger UI Documentation: [http://localhost:8000/docs](http://localhost:8000/docs)
* **osTicket Web Interface**: [http://localhost:8080](http://localhost:8080)
  * **Admin Panel**: [http://localhost:8080/scp](http://localhost:8080/scp)
  * **Admin Username**: `admin`
  * **Admin Password**: `changeme-admin` (You can change this in the `.env` file before running `make start`)

### Manual osTicket Setup (Optional)
If you prefer to go through the classic osTicket web installation wizard instead of the automated headless installation:
1. Run `make init` to generate your `.env` file.
2. Edit the `.env` file and set `OSTICKET_AUTO_SETUP=false`.
3. Run `make start`.
4. Visit [http://localhost:8080](http://localhost:8080) and complete the setup wizard.
5. Once completed, log into the Admin Panel, navigate to **Manage -> Plugins**, and install/enable the `fastapi_integration` plugin manually.

## Development

If you wish to develop the FastAPI application outside of Docker:
```bash
make install      # Install dependencies using uv
make dev          # Run the local uvicorn server with hot-reloading
make test         # Run the integration test suite
```
