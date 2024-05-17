import dolfinx
import ufl
from ufl import (FacetNormal, Identity, TestFunction, TrialFunction,
                 div, dot, ds, dx, inner, lhs, nabla_grad, rhs, sym)
import basix
import numpy as np
from mpi4py import MPI
from petsc4py import PETSc
import petsc4py
import fene_p_parameters
import dolfinx.fem.petsc


class Fene_P:
    def __init__(self, parameters: fene_p_parameters):
        # constants
        self.t = 0
        self.dim_u = None
        self.dim_sigma = None
        self.Re = None
        self.Wi = None
        self.regularized_model = False

        # functions
        self.colon_operator = None
        self.beta_d = None
        self.A_d = None
        self.f_n = None
        self.phi_lim = None
        self.jump_value = None
        self.problem = None
        self.eps = None

    def init_parameters_and_functions(self, parameters: fene_p_parameters):
        self.dim_u = parameters.Nx
        self.dim_sigma = parameters.Ny
        self.Re = parameters.L0 * parameters.U0 / (parameters.viscosity_s + parameters.viscosity_p)
        self.Wi = parameters.lambd * parameters.U0 / parameters.L0
        self.eps = parameters.eps

        self.colon_operator = lambda X, Y: ufl.tr(ufl.transpose(X) * Y)

        self.A = lambda phi: ufl.inv((1 / (1 - ufl.tr(phi) / parameters.b))) * ufl.Identity(self.dim_sigma) - ufl.inv(
            phi)
        self.f = [0,0]

    def mesh(self, parameters: fene_p_parameters):
        comm = MPI.COMM_WORLD
        # Create mesh
        return dolfinx.mesh.create_rectangle(comm, [np.array([0., 0.]), np.array([parameters.Lx, parameters.Ly])],
                                             [self.dim_u, self.dim_sigma], dolfinx.mesh.CellType.triangle)

    def init_model(self, mesh, parameters: fene_p_parameters):
        # function spaces
        v_el = basix.ufl.element("Lagrange", mesh.topology.cell_name(), 2, shape=(mesh.geometry.dim,))
        s_el = basix.ufl.element("Lagrange", mesh.topology.cell_name(), 1, shape=(mesh.geometry.dim,mesh.geometry.dim))
        V_h_0 = dolfinx.fem.functionspace(mesh, v_el)  # dolfinx.fem.function.ElementMetaData("CG", 1))
        S_h_0 = dolfinx.fem.functionspace(mesh, s_el)  # dolfinx.fem.function.ElementMetaData("CG", 0))

        # trial and test functions
        #u = ufl.TrialFunction(V_h_0)  # dolfinx.fem.Constant(domain, petsc4py.PETSc.ScalarType(0))
        sigma = ufl.TrialFunction(S_h_0)
        v = ufl.TestFunction(V_h_0)
        phi = ufl.TestFunction(S_h_0)

        # init conditions
        def y0_init(x):
            values = np.zeros((1, x.shape[1]))
            values[0] = 0.0
            return values

        def y1_init(x):
            values = np.zeros((1, x.shape[1]))
            values[0] = 0.0
            return values

        class vector_field_0:
            def __init__(self):
                self.t = 0.0

            def eval(self, x):
                return (x[1]-0.5)**4
        class vector_field_1:
            def __init__(self):
                self.t = 0.0

            def eval(self, x):
                return 0.0
        # def vector_field(x):
        #     values = np.zeros((2, x.shape[1]))
        #     values[0] = (x[1]-0.5)**4
        #     values[1] = 0.0
        #     return values

        x = ufl.SpatialCoordinate(mesh)
        u = dolfinx.fem.Function(V_h_0)
        vf_0 = vector_field_0()
        vf_1 = vector_field_1()
        vf_0.t =0.0
        vf_1.t =0.0
        #u = dolfinx.fem.Expression((x[1]-0.5)**4, V_h_0.element.interpolation_points())
        u.sub(0).interpolate(vf_0.eval)
        u.sub(1).interpolate(vf_0.eval)
        #from IPython import embed; embed()
        # Define initial condition
        u_init = dolfinx.fem.Function(V_h_0)
        u_init.sub(0).interpolate(y0_init)
        u_init.sub(1).interpolate(y1_init)

        sigma_init = dolfinx.fem.Function(S_h_0)
        sigma_init.sub(0).interpolate(y0_init)
        sigma_init.sub(1).interpolate(y1_init)
        sigma_init.sub(2).interpolate(y1_init)
        sigma_init.sub(3).interpolate(y1_init)

        f_n = dolfinx.fem.Function(V_h_0)
        f_n.sub(0).interpolate(y0_init)
        f_n.sub(1).interpolate(y1_init)

        dt = parameters.T / parameters.num_time_steps

        # Define boundary conditions
        def outflow(x):
            return np.logical_or(np.isclose(x[0], 0), np.isclose(x[0], 1))
        sigmaD = dolfinx.fem.Function(S_h_0)
        sigmaD.sub(0).interpolate(lambda x: (x[1]-0.5)**4)
        sigmaD.sub(1).interpolate(lambda x: (x[1]-0.5)**4)
        sigmaD.sub(2).interpolate(lambda x: (x[1]-0.5)**4)
        sigmaD.sub(3).interpolate(lambda x: (x[1]-0.5)**4)
        bc = dolfinx.fem.dirichletbc(sigmaD,
                                     dolfinx.fem.locate_dofs_geometrical(S_h_0, outflow))

        """
        P1 = self.Re * dot((u - u_init) / dt, v) * dx
        P1 -= self.Re * self.colon_operator(ufl.dot(u, u), ufl.grad(v)) * dx
        P1 += (1 - parameters.eps) * self.colon_operator(ufl.grad(u), ufl.grad(v)) * dx
        P1 += parameters.eps / self.Wi * ((self.colon_operator(
            (1. - ufl.tr(sigma) / parameters.b) * sigma, ufl.grad(v))) * dx)
        P1 -= dot(f_n, v) * dx
        #
        s11 = ufl.TrialFunction(S_h_0)
        s11_init = dolfinx.fem.Function(S_h_0)
        s11_init.sub(0).interpolate(y0_init)
        s11_init.sub(1).interpolate(y1_init)
        p11 = ufl.TestFunction(S_h_0)
        
        s12 = ufl.TrialFunction(S_h_0)
        s12_init = dolfinx.fem.Function(S_h_0)
        s12_init.sub(0).interpolate(y0_init)
        s12_init.sub(1).interpolate(y1_init)
        p12 = ufl.TestFunction(S_h_0)
        
        s21 = ufl.TrialFunction(S_h_0)
        s21_init = dolfinx.fem.Function(S_h_0)
        s21_init.sub(0).interpolate(y0_init)
        s21_init.sub(1).interpolate(y1_init)
        p21 = ufl.TestFunction(S_h_0)
        
        s22 = ufl.TrialFunction(S_h_0)
        s22_init = dolfinx.fem.Function(S_h_0)
        s22_init.sub(0).interpolate(y0_init)
        s22_init.sub(1).interpolate(y1_init)
        p22 = ufl.TestFunction(S_h_0)
        
        
        P2 = dot((sigma - sigma_init) / dt, phi) * dx
        P2 += dot(dot(u, ufl.grad(u)) * sigma, phi) * dx
        P2 -= ufl.grad(u) * sigma * phi * dx
        P2 -= sigma * ufl.transpose(ufl.grad(u)) * phi * dx
        P2 += self.A(sigma) * sigma / self.Wi * phi * dx
        """

        dets = ufl.det(sigma)
        trs = ufl.tr(sigma)

        lhs1 = ((sigma[0,0]-sigma_init[0,0])*phi[0,0] + (sigma[0,1] - sigma_init[0,1])*phi[1,0])*dx
        rhs1 = -((u[0]*sigma[0,0].dx(0) + u[1]*sigma[0,1].dx(1))*phi[0,0]+(u[0]*sigma[0,1].dx(0)+u[1]*sigma[0,1].dx(1))*phi[1,0])*dx
        rhs1 += (u[0].dx(0)*(sigma[0,0]*phi[0,0] + sigma[0,1]*phi[1,0]) + u[0].dx(1)*(sigma[1,0]*phi[0,0] + sigma[1,1]*phi[1,0]))*dx
        rhs1 += (sigma[0,0]*(u[0].dx(0)*phi[0,0]+u[1].dx(1)*phi[1,0])+ sigma[0,1]*(u[1].dx(0)*phi[0,0]+u[1].dx(1)*phi[1,0]))*dx
        rhs1 -= (((sigma[0,0]*(ufl.inv(1-trs)-sigma[1,0]/dets)+sigma[1,0]*ufl.Constant(sigma[0,1])/dets)*phi[0,0] +
                 (sigma[0,1]*(ufl.inv(1-trs)-ufl.Constant(sigma[1,1])/dets)+sigma[1,1]*ufl.Constant(sigma[0,1])/dets)*phi[1,0])/self.Wi)*dx

        lhs2 = ((sigma[0,0]-sigma_init[0,0])*phi[0,1] + (sigma[0,1] - sigma_init[0,1])*phi[1,1])*dx
        rhs2 = -((u[0]*sigma[0,0].dx(0) + u[1]*sigma[0,1].dx(1))*phi[0,1]+(u[0]*sigma[0,1].dx(0)+u[1]*sigma[0,1].dx(1))*phi[1,1])*dx
        rhs2 += (u[0].dx(0)*(sigma[0,0]*phi[0,1] + sigma[0,1]*phi[1,1]) + u[0].dx(1)*(sigma[1,0]*phi[0,1] + sigma[1,1]*phi[1,1]))*dx
        rhs2 += (sigma[0,0]*(u[0].dx(0)*phi[0,1]+u[1].dx(1)*phi[1,1])+ sigma[0,1]*(u[1].dx(0)*phi[0,1]+u[1].dx(1)*phi[1,1]))*dx
        rhs2 -= (((sigma[0, 0] * (ufl.inv(1 - trs) - sigma[1, 0] / dets) + sigma[1, 0] * sigma[0, 1] / dets) * phi[
            0, 1] +
                 (sigma[0, 1] * (ufl.inv(1 - trs) - sigma[1, 1] / dets) + sigma[1, 1] * sigma[0, 1] / dets) * phi[
                     1, 1])/ self.Wi) * dx

        lhs3 = ((sigma[1,0]-sigma_init[1,0])*phi[0,0] + (sigma[1,1] - sigma_init[1,1])*phi[1,0])*dx
        rhs3 = -((u[0]*sigma[1,0].dx(0) + u[1]*sigma[1,0].dx(1))*phi[0,0]+(u[0]*sigma[1,1].dx(0)+u[1]*sigma[1,1].dx(1))*phi[1,0])*dx
        rhs3 += (u[1].dx(0)*(sigma[0,0]*phi[0,0] + sigma[0,1]*phi[1,0]) + u[1].dx(1)*(sigma[1,0]*phi[1,1] + sigma[1,1]*phi[1,0]))*dx
        rhs3 += (sigma[1,0]*(u[0].dx(0)*phi[0,0]+u[1].dx(1)*phi[1,0])+ sigma[1,1]*(u[1].dx(0)*phi[0,0]+u[1].dx(1)*phi[1,0]))*dx
        rhs3 -= (((sigma[1, 0] * sigma[0, 0] / dets + sigma[0, 0] * (ufl.inv(1 - trs) - sigma[0, 0] / dets)) * phi[
            0, 0] +
                 (sigma[1, 0] * sigma[0, 1] / dets + sigma[1, 1] * (ufl.inv(1 - trs) - sigma[1, 1] / dets)) * phi[
                     1, 0])/ self.Wi) * dx

        lhs4 = ((sigma[1, 0] - sigma_init[1, 0]) * phi[0, 1] + (sigma[1, 1] - sigma_init[1, 1]) * phi[1, 1]) * dx
        rhs4 = -((u[0] * sigma[1, 0].dx(0) + u[1] * sigma[1, 0].dx(1)) * phi[0, 1] + (
                    u[0] * sigma[1, 1].dx(0) + u[1] * sigma[1, 1].dx(1)) * phi[1, 1]) * dx
        rhs4 += (u[1].dx(0) * (sigma[0, 0] * phi[0, 1] + sigma[0, 1] * phi[1, 1]) + u[1].dx(1) * (
                    sigma[1, 0] * phi[1, 0] + sigma[1, 1] * phi[1, 1])) * dx
        rhs4 += (sigma[1, 0] * (u[0].dx(0) * phi[0, 1] + u[1].dx(1) * phi[1, 1]) + sigma[1, 1] * (
                    u[1].dx(0) * phi[0, 1] + u[1].dx(1) * phi[1, 1])) * dx
        rhs4 -= (((sigma[1, 0] * sigma[0, 0] / dets + sigma[1, 0] * (ufl.inv(1 - trs) - sigma[0, 0] / dets)) * phi[
            0, 1] +
                 (sigma[1, 0] * sigma[0, 1] / dets + sigma[1, 1] * (ufl.inv(1 - trs) - sigma[1, 1] / dets)) * phi[
                     1, 1])/ self.Wi) * dx

        lhs = [lhs1, lhs2, lhs3, lhs4]
        rhs = [rhs1, rhs2, rhs3, rhs4]
        #F = lhs - rhs

        return V_h_0, S_h_0, lhs, rhs, u, sigma, u_init, bc

    def solve(self, parameters: fene_p_parameters, mesh, V, S, lhs, rhs, u, sigma, u_n, bc):
        #from IPython import embed
        #embed()
        problem = dolfinx.fem.petsc.LinearProblem(lhs, rhs, bcs=[bc], petsc_options={"ksp_type": "gmres", "ksp_rtol": 1e-6, "ksp_atol": 1e-10, "ksp_max_it": 1000, "pc_type": "none"})

        sigmah = problem.solve()
        lu_solver = problem.solver
        viewer = PETSc.Viewer().createASCII("gmres_output.txt")
        lu_solver.view(viewer)
        solver_output = open("gmres_output.txt", "r")
        for line in solver_output.readlines():
            print(line)
        t = t_n = 0
        dt = parameters.T / parameters.num_time_steps
        #from IPython import embed;
        #embed()
        #np.zeros((parameters.num_time_steps + 1), V.dim()),
        solution_data = [np.zeros((parameters.num_time_steps + 1,2,2))]
        time_values_data = np.zeros(parameters.num_time_steps + 1)

        for time_step in range(parameters.num_time_steps):
            t += dt
            # dolfinx.fem.petsc.assemble_vector(P1, P2)
            # problem.solve(F, sigma, bc)
            #solution_data[0][t + 1, :] = np.array(u.vector())
            solution_data[0][t + 1, :] = np.array(sigma.vector())
            time_values_data[t + 1] = t

            u_n.assign(u)
            t_n = t

        return solution_data, time_values_data

    def run(self,parameters):
        self.init_parameters_and_functions(parameters)
        mesh = self.mesh(parameters)
        V, S, lhs, rhs, u, sigma, u_init, bc = self.init_model(mesh, parameters)
        return self.solve(parameters, mesh, V, S, lhs, rhs, u, sigma, u_init, bc)

    # Plot solution
    def plot(self, mesh, P):
        import dolfinx.plot

        dolfinx.plot.create_vtk_topology(mesh, 2)
        dolfinx.plot.plot(P)

        # Hold plot
        import matplotlib.pyplot as plt

        plt.show()


params = fene_p_parameters.ModelParameters()
model = Fene_P(params)
sol, t = model.run(params)

