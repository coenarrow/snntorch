import torch
import torch.nn as nn
import numpy as np


device = torch.device("cuda") if torch.cuda.is_available() else torch.device("cpu")
dtype = torch.float
slope = 25


class LIF(nn.Module):
    """Parent class for leaky integrate and fire neuron models."""

    instances = []
    """Each :mod:`snntorch.LIF` neuron (e.g., :mod:`snntorch.Stein`) will populate the :mod:`snntorch.LIF.instances` list with a new entry.
    The list is used to initialize and clear neuron states when the argument `init_hidden=True`."""

    def __init__(self, alpha, beta, threshold=1.0, spike_grad=None, inhibition=False):
        super(LIF, self).__init__()
        LIF.instances.append(self)

        self.alpha = alpha
        self.beta = beta
        self.threshold = threshold
        self.inhibition = inhibition

        if spike_grad is None:
            self.spike_grad = self.Heaviside.apply
        else:
            self.spike_grad = spike_grad

    def fire(self, mem):
        """Generates spike if mem > threshold.
        Returns spk and reset."""
        mem_shift = mem - self.threshold
        spk = self.spike_grad(mem_shift).to(device)
        reset = spk.clone().detach()
        return spk, reset

    def fire_inhibition(self, batch_size, mem):
        """Generates spike if mem > threshold, only for the largest membrane. All others neurons will be inhibited for that time step.
        Returns spk and reset."""
        mem_shift = mem - self.threshold
        index = torch.argmax(mem_shift, dim=1)
        spk_tmp = self.spike_grad(mem_shift)

        mask_spk1 = torch.zeros_like(spk_tmp)
        mask_spk1[torch.arange(batch_size), index] = 1
        spk = spk_tmp * mask_spk1.to(device)

        reset = spk.clone().detach()
        return spk, reset

    @classmethod
    def clear_instances(cls):
        """Removes all items from :mod:`snntorch.LIF.instances` when called."""
        cls.instances = []

    @staticmethod
    def init_stein(batch_size, *args):
        """Used to initialize syn, mem and spk.
        *args are the input feature dimensions.
        E.g., ``batch_size=128`` and input feature of size=1x28x28 would require ``init_stein(128, 1, 28, 28)``."""
        syn = torch.zeros((batch_size, *args), device=device, dtype=dtype)
        mem = torch.zeros((batch_size, *args), device=device, dtype=dtype)
        spk = torch.zeros((batch_size, *args), device=device, dtype=dtype)

        return spk, syn, mem

    @staticmethod
    def init_srm0(batch_size, *args):
        """Used to initialize syn_pre, syn_post, mem and spk.
        *args are the input feature dimensions.
        E.g., ``batch_size=128`` and input feature of size=1x28x28 would require ``init_srm0(128, 1, 28, 28).``"""
        syn_pre = torch.zeros((batch_size, *args), device=device, dtype=dtype)
        syn_post = torch.zeros((batch_size, *args), device=device, dtype=dtype)
        mem = torch.zeros((batch_size, *args), device=device, dtype=dtype)
        spk = torch.zeros((batch_size, *args), device=device, dtype=dtype)

        return spk, syn_pre, syn_post, mem

    @staticmethod
    def detach(*args):
        """Used to detach input arguments from the current graph.
        Intended for use in truncated backpropagation through time where hidden state variables are global variables."""
        for state in args:
            state.detach_()

    @staticmethod
    def zeros(*args):
        """Used to clear hidden state variables to zero.
        Intended for use where hidden state variables are global variables."""
        for state in args:
            state = torch.zeros_like(state)

    @staticmethod
    class Heaviside(torch.autograd.Function):
        """Default spiking function for neuron.

        **Forward pass:** Heaviside step function shifted.

        .. math::

            S=\\begin{cases} 1 & \\text{if U ≥ U$_{\\rm thr}$} \\\\
            0 & \\text{if U < U$_{\\rm thr}$}
            \\end{cases}

        **Backward pass:** Heaviside step function shifted.

        .. math::

            \\frac{∂S}{∂U}=\\begin{cases} 1 & \\text{if U ≥ U$_{\\rm thr}$} \\\\
            0 & \\text{if U < U$_{\\rm thr}$}
            \\end{cases}

        Although the backward pass is clearly not the analytical solution of the forward pass, this assumption holds true on the basis that a reset necessarily occurs after a spike is generated when :math:`U ≥ U_{\\rm thr}`."""

        @staticmethod
        def forward(ctx, input_):
            ctx.save_for_backward(input_)
            out = torch.zeros_like(input_)
            out[input_ >= 0] = 1.0
            return out

        @staticmethod
        def backward(ctx, grad_output):
            (input_,) = ctx.saved_tensors
            grad_input = grad_output.clone()
            grad_input[input_ < 0] = 0.0
            grad = grad_input
            return grad


# Neuron Models


class Stein(LIF):
    """
    Stein's model of the leaky integrate and fire neuron.
    The synaptic current jumps upon spike arrival, which causes a jump in membrane potential.
    Synaptic current and membrane potential decay exponentially with rates of alpha and beta, respectively.
    For :math:`U[T] > U_{\\rm thr} ⇒ S[T+1] = 1`.

    .. math::

            I_{\\rm syn}[t+1] = αI_{\\rm syn}[t] + I_{\\rm in}[t+1] \\\\
            U[t+1] = βU[t] + I_{\\rm syn}[t+1] - R

    * :math:`I_{\\rm syn}` - Synaptic current
    * :math:`I_{\\rm in}` - Input current
    * :math:`U` - Membrane potential
    * :math:`R` - Reset mechanism
    * :math:`α` - Synaptic current decay rate
    * :math:`β` - Membrane potential decay rate

    Example::

        import torch
        import torch.nn as nn
        import snntorch as snn

        alpha = 0.9
        beta = 0.5

        # Define Network
        class Net(nn.Module):
            def __init__(self):
                super().__init__()

                # initialize layers
                self.fc1 = nn.Linear(num_inputs, num_hidden)
                self.lif1 = snn.Stein(alpha=alpha, beta=beta)
                self.fc2 = nn.Linear(num_hidden, num_outputs)
                self.lif2 = snn.Stein(alpha=alpha, beta=beta)

            def forward(self, x, syn1, mem1, spk1, syn2, mem2):
                cur1 = self.fc1(x)
                spk1, syn1, mem1 = self.lif1(cur1, syn1, mem1)
                cur2 = self.fc2(spk1)
                spk2, syn2, mem2 = self.lif2(cur2, syn2, mem2)
                return syn1, mem1, spk1, syn2, mem2, spk2


    For further reading, see:

    *R. B. Stein (1965) A theoretical analysis of neuron variability. Biophys. J. 5, pp. 173-194.*

    *R. B. Stein (1967) Some models of neuronal variability. Biophys. J. 7. pp. 37-68.*"""

    def __init__(
        self,
        alpha,
        beta,
        threshold=1.0,
        num_inputs=False,
        spike_grad=None,
        batch_size=False,
        hidden_init=False,
        inhibition=False,
    ):
        super(Stein, self).__init__(alpha, beta, threshold, spike_grad, inhibition)

        self.num_inputs = num_inputs
        self.batch_size = batch_size
        self.hidden_init = hidden_init

        if self.hidden_init:
            if not self.num_inputs:
                raise ValueError(
                    "num_inputs must be specified to initialize hidden states as instance variables."
                )
            elif not self.batch_size:
                raise ValueError(
                    "batch_size must be specified to initialize hidden states as instance variables."
                )
            elif hasattr(self.num_inputs, "__iter__"):
                self.spk, self.syn, self.mem = self.init_stein(
                    self.batch_size, *(self.num_inputs)
                )  # need to automatically call batch_size
            else:
                self.spk, self.syn, self.mem = self.init_stein(
                    self.batch_size, self.num_inputs
                )
        if self.inhibition:
            if not self.batch_size:
                raise ValueError(
                    "batch_size must be specified to enable firing inhibition."
                )

    def forward(self, input_, syn, mem):
        if not self.hidden_init:
            if self.inhibition:
                spk, reset = self.fire_inhibition(self.batch_size, mem)
            else:
                spk, reset = self.fire(mem)
            syn = self.alpha * syn + input_
            mem = self.beta * mem + syn - reset

            return spk, syn, mem

        # intended for truncated-BPTT where instance variables are hidden states
        if self.hidden_init:
            if self.inhibition:
                self.spk, self.reset = self.fire_inhibition(self.batch_size, self.mem)
            else:
                self.spk, self.reset = self.fire(self.mem)
            self.syn = self.alpha * self.syn + input_
            self.mem = self.beta * self.mem + self.syn - self.reset

            return self.spk, self.syn, self.mem

    @classmethod
    def detach_hidden(cls):
        """Returns the hidden states, detached from the current graph.
        Intended for use in truncated backpropagation through time where hidden state variables are instance variables."""

        for layer in range(len(cls.instances)):
            if isinstance(cls.instances[layer], Stein):
                cls.instances[layer].spk.detach_()
                cls.instances[layer].syn.detach_()
                cls.instances[layer].mem.detach_()

    @classmethod
    def zeros_hidden(cls):
        """Used to clear hidden state variables to zero.
        Intended for use where hidden state variables are instance variables."""

        for layer in range(len(cls.instances)):
            if isinstance(cls.instances[layer], Stein):
                cls.instances[layer].spk = torch.zeros_like(cls.instances[layer].spk)
                cls.instances[layer].syn = torch.zeros_like(cls.instances[layer].syn)
                cls.instances[layer].mem = torch.zeros_like(cls.instances[layer].mem)


class SRM0(LIF):
    """
    Simplified Spike Response Model (:math:`0^{\\rm th}`` order) of the leaky integrate and fire neuron.
    The time course of the membrane potential response depends on a combination of exponentials.
    In general, this causes the change in membrane potential to experience a delay with respect to an input spike.
    For :math:`U[T] > U_{\\rm thr} ⇒ S[T+1] = 1`.

    .. warning:: For a positive input current to induce a positive membrane response, ensure :math:`α > β`.

    .. math::

            I_{\\rm syn-pre}[t+1] = (αI_{\\rm syn-pre}[t] + I_{\\rm in}[t+1])(1-R) \\\\
            I_{\\rm syn-post}[t+1] = (βI_{\\rm syn-post}[t] - I_{\\rm in}[t+1])(1-R) \\\\
            U[t+1] = τ_{\\rm SRM}(I_{\\rm syn-pre}[t+1] + I_{\\rm syn-post}[t+1])(1-R)

    * :math:`I_{\\rm syn-pre}` - Pre-synaptic current
    * :math:`I_{\\rm syn-post}` - Post-synaptic current
    * :math:`I_{\\rm in}` - Input current
    * :math:`U` - Membrane potential
    * :math:`R` - Reset mechanism
    * :math:`α` - Pre-synaptic current decay rate
    * :math:`β` - Post-synaptic current decay rate
    * :math:`τ_{\\rm SRM} = \\frac{log(α)}{log(β)} - log(α) + 1`

    Example::

        import torch
        import torch.nn as nn
        import snntorch as snn

        alpha = 0.9
        beta = 0.8

        # Define Network
        class Net(nn.Module):
            def __init__(self):
                super().__init__()

                # initialize layers
                self.fc1 = nn.Linear(num_inputs, num_hidden)
                self.lif1 = snn.SRM0(alpha=alpha, beta=beta)
                self.fc2 = nn.Linear(num_hidden, num_outputs)
                self.lif2 = snn.SRM0(alpha=alpha, beta=beta)

            def forward(self, x, presyn1, postsyn1, mem1, spk1, presyn2, postsyn2, mem2):
                cur1 = self.fc1(x)
                spk1, presyn1, postsyn1, mem1 = self.lif1(cur1, presyn1, postsyn1, mem1)
                cur2 = self.fc2(spk1)
                spk2, presyn2, postsyn2, mem2 = self.lif2(cur2, presyn2, postsyn2, mem2)
                return presyn1, postsyn1, mem1, spk1, presyn2, postsyn2, mem2, spk2


    For further reading, see:

    *R. Jovilet, J. Timothy, W. Gerstner (2003) The spike response model: A framework to predict neuronal spike trains. Artificial Neural Networks and Neural Information Processing, pp. 846-853.*"""

    def __init__(
        self,
        alpha,
        beta,
        threshold=1.0,
        num_inputs=False,
        spike_grad=None,
        batch_size=False,
        hidden_init=False,
        inhibition=False,
    ):
        super(SRM0, self).__init__(alpha, beta, threshold, spike_grad, inhibition)

        self.num_inputs = num_inputs
        self.batch_size = batch_size
        self.hidden_init = hidden_init

        if self.hidden_init:
            if not self.num_inputs:
                raise ValueError(
                    "num_inputs must be specified to initialize hidden states as instance variables."
                )
            elif not self.batch_size:
                raise ValueError(
                    "batch_size must be specified to initialize hidden states as instance variables."
                )
            elif hasattr(self.num_inputs, "__iter__"):
                self.spk, self.syn_pre, self.syn_post, self.mem = self.init_srm0(
                    batch_size=self.batch_size, *(self.num_inputs)
                )
            else:
                self.spk, self.syn_pre, self.syn_post, self.mem = self.init_srm0(
                    batch_size, num_inputs
                )
        if self.inhibition:
            if not self.batch_size:
                raise ValueError(
                    "batch_size must be specified to enable firing inhibition."
                )

        if self.alpha <= self.beta:
            raise ValueError("alpha must be greater than beta.")

        if self.beta == 1:
            raise ValueError(
                "beta cannot be '1' otherwise ZeroDivisionError occurs: tau_srm = log(alpha)/log(beta) - log(alpha) + 1"
            )

        self.tau_srm = np.log(self.alpha) / (np.log(self.beta) - np.log(self.alpha)) + 1

    def forward(self, input_, syn_pre, syn_post, mem):
        # if hidden states are passed externally
        if not self.hidden_init:
            if self.inhibition:
                spk, reset = self.fire_inhibition(self.batch_size, mem)
            else:
                spk, reset = self.fire(mem)
            syn_pre = (self.alpha * syn_pre + input_) * (1 - reset)
            syn_post = (self.beta * syn_post - input_) * (1 - reset)
            mem = self.tau_srm * (syn_pre + syn_post) * (1 - reset) + (
                mem * reset - reset
            )
            return spk, syn_pre, syn_post, mem

        # if hidden states and outputs are instance variables
        if self.hidden_init:
            if self.inhibition:
                self.spk, self.reset = self.fire_inhibition(self.batch_size, self.mem)
            else:
                self.spk, self.reset = self.fire(self.mem)
            self.syn_pre = (self.alpha * self.syn_pre + input_) * (1 - self.reset)
            self.syn_post = (self.beta * self.syn_post - input_) * (1 - self.reset)
            self.mem = self.tau_srm * (self.syn_pre + self.syn_post) * (
                1 - self.reset
            ) + (self.mem * self.reset - self.reset)
            return self.spk, self.syn_pre, self.syn_post, self.mem

    # cool forward function that resulted in burst firing - worth exploring

    # def forward(self, input_, syn_pre, syn_post, mem):
    #     mem_shift = mem - self.threshold
    #     spk = self.spike_grad(mem_shift).to(device)
    #     reset = torch.zeros_like(mem)
    #     spk_idx = (mem_shift > 0)
    #     reset[spk_idx] = torch.ones_like(mem)[spk_idx]
    #
    #     syn_pre = self.alpha * syn_pre + input_
    #     syn_post = self.beta * syn_post - input_
    #     mem = self.tau_srm * (syn_pre + syn_post) - reset

    # return spk, syn_pre, syn_post, mem

    @classmethod
    def detach_hidden(cls):
        """Used to detach hidden states from the current graph.
        Intended for use in truncated backpropagation through
        time where hidden state variables are instance variables."""
        for layer in range(len(cls.instances)):
            if isinstance(cls.instances[layer], SRM0):
                cls.instances[layer].spk.detach_()
                cls.instances[layer].syn_pre.detach_()
                cls.instances[layer].syn_post.detach_()
                cls.instances[layer].mem.detach_()

    @classmethod
    def zeros_hidden(cls):
        """Used to clear hidden state variables to zero.
        Intended for use where hidden state variables are instance variables."""
        for layer in range(len(cls.instances)):
            if isinstance(cls.instances[layer], SRM0):
                cls.instances[layer].spk = torch.zeros_like(cls.instances[layer].spk)
                cls.instances[layer].syn_pre = torch.zeros_like(
                    cls.instances[layer].syn_pre
                )
                cls.instances[layer].syn_post = torch.zeros_like(
                    cls.instances[layer].syn_post
                )
                cls.instances[layer].mem = torch.zeros_like(cls.instances[layer].mem)
