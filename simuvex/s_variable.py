import collections
import claripy

class SimVariable(object):
    def __init__(self, ident=None, name=None, phi=False, region=None):
        """
        :param ident: A unique identifier provided by user or the program. Usually a string.
        :param str name: Name of this variable.
        """
        self.ident = ident
        self.name = name
        self.phi = phi
        self.region = region if region is not None else ""


class SimConstantVariable(SimVariable):
    def __init__(self, ident=None, value=None, region=None):
        super(SimConstantVariable, self).__init__(ident=ident, region=region)
        self.value = value

    def __repr__(self):
        s = "<%s|const %s>" % (self.region, self.value)

        return s

    def __eq__(self, other):
        if not isinstance(other, SimConstantVariable):
            return False

        if self.value is None or other.value is None:
            # they may or may not represent the same constant. return not equal to be safe
            return False

        return self.value == other.value and self.ident == other.ident and self.region == other.region

    def __hash__(self):
        return hash(('const', self.value, self.ident, self.region))


class SimTemporaryVariable(SimVariable):
    def __init__(self, tmp_id):
        SimVariable.__init__(self)

        self.tmp_id = tmp_id

    def __repr__(self):
        s = "<tmp %d>" % (self.tmp_id)

        return s

    def __hash__(self):
        return hash('tmp_%d' % (self.tmp_id))

    def __eq__(self, other):
        if isinstance(other, SimTemporaryVariable):
            return hash(self) == hash(other)

        else:
            return False


class SimRegisterVariable(SimVariable):
    def __init__(self, reg_offset, size, ident=None, name=None, region=None):
        SimVariable.__init__(self, ident=ident, name=name, region=region)

        self.reg = reg_offset
        self.size = size

    def __repr__(self):
        s = "<%s|Reg %s %s>" % (self.region, self.reg, self.size)

        return s

    def __hash__(self):
        return hash('%s|reg_%s_%s_%s' % (self.region, self.reg, self.size, self.ident))

    def __eq__(self, other):
        if isinstance(other, SimRegisterVariable):
            return hash(self) == hash(other) and self.name == other.name and self.ident == other.ident and \
                   self.region == other.region

        else:
            return False


class SimMemoryVariable(SimVariable):
    def __init__(self, addr, size, ident=None, name=None, phi=False, region=None):
        SimVariable.__init__(self, ident=ident, name=name, phi=phi, region=region)

        self.addr = addr

        if isinstance(size, claripy.ast.BV) and not size.symbolic:
            # Convert it to a concrete number
            size = size._model_concrete.value

        self.size = size

    def __repr__(self):
        if type(self.size) in (int, long):
            size = '%d' % self.size
        else:
            size = '%s' % self.size

        if type(self.addr) in (int, long):
            s = "<%s|Mem %#x %s>" % (self.region, self.addr, size)
        else:
            s = "<%s|Mem %s %s>" % (self.region, self.addr, size)

        return s

    def __hash__(self):
        if isinstance(self.addr, AddressWrapper):
            addr_hash = hash(self.addr)
        elif type(self.addr) in (int, long):
            addr_hash = self.addr
        elif self.addr._model_concrete is not self.addr:
            addr_hash = hash(self.addr._model_concrete)
        elif self.addr._model_vsa is not self.addr:
            addr_hash = hash(self.addr._model_vsa)
        elif self.addr._model_z3 is not self.addr:
            addr_hash = hash(self.addr._model_z3)
        else:
            addr_hash = hash(self.addr)
        return hash((addr_hash, hash(self.size), self.ident))

    def __eq__(self, other):
        if isinstance(other, SimMemoryVariable):
            return hash(self) == hash(other) and self.name == other.name

        else:
            return False


class SimStackVariable(SimMemoryVariable):
    def __init__(self, offset, size, base='sp', base_addr=None, ident=None, name=None, phi=False, region=None):
        if offset > 0x1000000 and isinstance(offset, (int, long)):
            # I don't think any positive stack offset will be greater than that...
            # convert it to a negative number
            mask = (1 << offset.bit_length()) - 1
            offset = - ((0 - offset) & mask)

        if base_addr is not None:
            addr = offset + base_addr
        else:
            # TODO: this is not optimal
            addr = offset

        super(SimStackVariable, self).__init__(addr, size, ident=ident, name=name, phi=phi, region=region)

        self.base = base
        self.offset = offset

    def __repr__(self):
        if type(self.size) in (int, long):
            size = '%d' % self.size
        else:
            size = '%s' % self.size

        prefix = "%s(stack)" % self.name if self.name is not None else "Stack"

        if type(self.offset) in (int, long):
            if self.offset < 0:
                offset = "%#x" % self.offset
            elif self.offset > 0:
                offset = "+%#x" % self.offset
            else:
                offset = ""

            s = "<%s|%s %s%s, %s bytes>" % (self.region, prefix, self.base, offset, size)
        else:
            s = "<%s|%s %s%s, %s bytes>" % (self.region, prefix, self.base, self.addr, size)

        return s


class SimVariableSet(collections.MutableSet):
    """
    A collection of SimVariables.
    """

    def __init__(self):

        self.register_variables = set()
        # For the sake of performance optimization, all elements in register_variables must be concrete integers which
        # representing register offsets..
        # There shouldn't be any problem apart from GetI/PutI instructions. We simply ignore them for now.
        # TODO: Take care of register offsets that are not aligned to (arch.bits/8)
        self.register_variable_offsets = set()

        # memory_variables holds SimMemoryVariable objects
        self.memory_variables = set()
        # For the sake of performance, we have another set that stores memory addresses of memory_variables
        self.memory_variable_addresses = set()

    def add(self, item):
        if type(item) is SimRegisterVariable:
            if not self.contains_register_variable(item):
                self.add_register_variable(item)
        elif type(item) is SimMemoryVariable:
            if not self.contains_memory_variable(item):
                self.add_memory_variable(item)
        else:
            # TODO:
            raise Exception('WTF')

    def add_register_variable(self, reg_var):
        self.register_variables.add(reg_var)
        self.register_variable_offsets.add(reg_var.reg)

    def add_memory_variable(self, mem_var):
        self.memory_variables.add(mem_var)
        base_address = mem_var.addr.address # Dealing with AddressWrapper
        for i in xrange(mem_var.size):
            self.memory_variable_addresses.add(base_address + i)

    def discard(self, item):
        if type(item) is SimRegisterVariable:
            if self.contains_register_variable(item):
                self.discard_register_variable(item)
        elif isinstance(item, SimMemoryVariable):
            if self.contains_memory_variable(item):
                self.discard_memory_variable(item)
        else:
            # TODO:
            raise Exception('')

    def discard_register_variable(self, reg_var):
        self.register_variables.remove(reg_var)
        self.register_variable_offsets.remove(reg_var.reg)

    def discard_memory_variable(self, mem_var):
        self.memory_variables.remove(mem_var)
        for i in xrange(mem_var.size):
            self.memory_variable_addresses.remove(mem_var.addr.address + i)

    def __len__(self):
        return len(self.register_variables) + len(self.memory_variables)

    def __iter__(self):
        for i in self.register_variables: yield i
        for i in self.memory_variables: yield i

    def add_memory_variables(self, addrs, size):
        for a in addrs:
            var = SimMemoryVariable(a, size)
            self.add_memory_variable(var)

    def copy(self):
        s = SimVariableSet()
        s.register_variables |= self.register_variables
        s.register_variable_offsets |= self.register_variable_offsets
        s.memory_variables |= self.memory_variables
        s.memory_variable_addresses |= self.memory_variable_addresses

        return s

    def complement(self, other):
        """
        Calculate the complement of `self` and `other`.

        :param other:   Another SimVariableSet instance.
        :return:        The complement result.
        """

        s = SimVariableSet()
        s.register_variables = self.register_variables - other.register_variables
        s.register_variable_offsets = self.register_variable_offsets - other.register_variable_offsets
        s.memory_variables = self.memory_variables - other.memory_variables
        s.memory_variable_addresses = self.memory_variable_addresses - other.memory_variable_addresses

        return s

    def contains_register_variable(self, reg_var):
        reg_offset = reg_var.reg
        # TODO: Make sure reg_offset is aligned to machine-word length

        return reg_offset in self.register_variable_offsets

    def contains_memory_variable(self, mem_var):
        a = mem_var.addr
        if type(a) in (tuple, list): a = a[-1]

        return a in self.memory_variable_addresses

    def __ior__(self, other):
        # other must be a SimVariableSet
        self.register_variables |= other.register_variables
        self.register_variable_offsets |= other.register_variable_offsets
        self.memory_variables |= other.memory_variables
        self.memory_variable_addresses |= other.memory_variable_addresses

    def __contains__(self, item):
        if type(item) is SimRegisterVariable:

            return self.contains_register_variable(item)

        elif type(item) is SimMemoryVariable:
            # TODO: Make it better!
            return self.contains_memory_variable(item)

        else:
            __import__('ipdb').set_trace()
            raise Exception("WTF is this variable?")

from .storage.memory import AddressWrapper
